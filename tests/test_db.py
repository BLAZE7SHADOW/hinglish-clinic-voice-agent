from agent.db import connect_kwargs


def test_raw_at_sign_in_password():
    kw = connect_kwargs(
        "postgresql://postgres.ref:p@ss@w0rd@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    )
    assert kw["password"] == "p@ss@w0rd"
    assert kw["host"] == "aws-0-ap-south-1.pooler.supabase.com"
    assert (kw["user"], kw["port"], kw["database"]) == ("postgres.ref", 5432, "postgres")


def test_percent_encoded_password_and_query_string():
    kw = connect_kwargs("postgresql://u:a%40b%23c@host:6543/db?sslmode=require")
    assert (kw["password"], kw["port"], kw["database"]) == ("a@b#c", 6543, "db")
