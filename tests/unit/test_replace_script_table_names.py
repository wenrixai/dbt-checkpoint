import pytest
import sqlparse
import sqlparse.exceptions

from dbt_checkpoint.replace_script_table_names import get_source_from_name, main

# Input, expected return value, expected output
TESTS = (  # type: ignore
    (
        """
    SELECT *, bb.replaced_model, replaced_model.aa FROM replaced_model
    """,
        1,
        """
    SELECT *, bb.replaced_model, replaced_model.aa FROM {{ ref('replaced_model') }}
    """,
        True,
        True,
    ),
    (
        """
    SELECT * FROM ff.replaced_model
    """,
        1,
        """
    SELECT * FROM {{ ref('replaced_model') }}
    """,
        True,
        True,
    ),
    (
        """
    SELECT * FROM {{ ref('replaced_model') }}
    """,
        0,
        """
    SELECT * FROM {{ ref('replaced_model') }}
    """,
        True,
        True,
    ),
    (
        """
    SELECT * FROM {{ ref('replaced_model') }}
    """,
        1,
        """
    SELECT * FROM {{ ref('replaced_model') }}
    """,
        False,
        True,
    ),
    (
        """
    SELECT * FROM replaced_model
    JOIN source1.table1
    """,
        1,
        """
    SELECT * FROM {{ ref('replaced_model') }}
    JOIN {{ source('source1', 'table1') }}
    """,
        True,
        True,
    ),
    (
        """
    SELECT * FROM replaced_model
    JOIN source1.table1
    JOIN ff.bb
    """,
        1,
        """
    SELECT * FROM {{ ref('replaced_model') }}
    JOIN {{ source('source1', 'table1') }}
    JOIN {{ source('ff', 'bb') }}
    """,
        True,
        True,
    ),
    (
        """
    SELECT * FROM replaced_model
    JOIN source1.table1
    JOIN ff.bb
    JOIN aa
    """,
        1,
        """
    SELECT * FROM {{ ref('replaced_model') }}
    JOIN {{ source('source1', 'table1') }}
    JOIN {{ source('ff', 'bb') }}
    JOIN aa
    """,
        True,
        True,
    ),
    (
        """
    SELECT * FROM replaced_model
    JOIN source1.table1
    JOIN source1.table2
    """,
        1,
        """
    SELECT * FROM {{ ref('replaced_model') }}
    JOIN {{ source('source1', 'table1') }}
    JOIN {{ source('source1', 'table2') }}
    """,
        True,
        True,
    ),
    (
        """
    SELECT * FROM replaced_model
    JOIN aa.source1.table1
    JOIN source3.table3
    JOIN source1.table2
    """,
        1,
        """
    SELECT * FROM {{ ref('replaced_model') }}
    JOIN {{ source('source1', 'table1') }}
    JOIN {{ source('source3', 'table3') }}
    JOIN {{ source('source1', 'table2') }}
    """,
        True,
        True,
    ),
    (
        """
    SELECT * FROM {{ ref('replaced_model') }}
    """,
        0,
        """
    SELECT * FROM {{ ref('replaced_model') }}
    """,
        True,
        False,
    ),
    # Comments are preserved verbatim, and every chunk between them is still
    # rewritten (a leading comment used to exhaust the replacement generator
    # before any real SQL was reached, silently replacing nothing).
    (
        """-- comment naming replaced_model stays as is
    SELECT * FROM replaced_model
    /* block comment naming source1.table1 stays as is */
    JOIN source1.table1 ON 1=1
    -- trailing comment naming replaced_model
    JOIN source1.table2 ON 1=1
    """,
        1,
        """-- comment naming replaced_model stays as is
    SELECT * FROM {{ ref('replaced_model') }}
    /* block comment naming source1.table1 stays as is */
    JOIN {{ source('source1', 'table1') }} ON 1=1
    -- trailing comment naming replaced_model
    JOIN {{ source('source1', 'table2') }} ON 1=1
    """,
        True,
        True,
    ),
)


@pytest.mark.parametrize(
    ("input_s", "expected_status_code", "output", "valid_manifest", "valid_config"),
    TESTS,
)
def test_replace_script_table_names(
    input_s,
    expected_status_code,
    output,
    valid_manifest,
    valid_config,
    manifest_path_str,
    config_path_str,
    tmpdir,
):
    path = tmpdir.join("file.txt")
    path.write_text(input_s, "utf-8")
    input_args = [str(path), "--is_test"]

    if valid_manifest:
        input_args.extend(["--manifest", manifest_path_str])

    if valid_config:
        input_args.extend(["--config", config_path_str])

    ret = main(input_args)

    assert ret == expected_status_code
    result = path.read_text(encoding="utf-8")
    assert result == output


def test_get_source_from_name(manifest):
    result = get_source_from_name(
        manifest, {"prod.source1.src3", "dev.source1.src3", "dev2.source1.src3"}
    )
    assert list(result) == [
        ("prod.source1.src3", "{{ source('source1', 'src3') }}"),
        ("dev2.source1.src3", "{{ source('source1', 'src3') }}"),
    ]


def test_replace_script_table_names_large_file(
    manifest_path_str, config_path_str, tmpdir
):
    """Models above sqlparse's 10000-token grouping limit must still be rewritten.

    ``sqlparse.parse`` raises ``SQLParseError: Maximum number of tokens
    exceeded (10000)`` on statements this large, which used to crash the hook
    and fail the whole pre-commit run (RND-17189).
    """
    columns = ",\n".join(f"coalesce(col_{i}, 0) as c_{i}" for i in range(3000))
    input_s = f"-- big model\nSELECT\n{columns}\nFROM replaced_model\n"
    expected = (
        f"-- big model\nSELECT\n{columns}\n"
        "FROM {{ ref('replaced_model') }}\n"
    )

    # Guard the premise: this input really does exceed the grouping limit.
    with pytest.raises(sqlparse.exceptions.SQLParseError):
        sqlparse.parse(input_s)

    path = tmpdir.join("big_model.sql")
    path.write_text(input_s, "utf-8")

    ret = main(
        [
            str(path),
            "--is_test",
            "--manifest",
            manifest_path_str,
            "--config",
            config_path_str,
        ]
    )

    assert ret == 1
    assert path.read_text(encoding="utf-8") == expected
