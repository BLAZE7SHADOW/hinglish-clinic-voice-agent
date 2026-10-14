from agent.vocabulary import keyterms_for


def test_keyterms_come_from_the_clinic_records():
    terms = keyterms_for(["Dr. Anil Sharma", "Dr. Priya Mehta", "Doctor K. Iyer"], "City Clinic")
    assert terms == [
        "Anil Sharma",
        "Sharma",
        "Priya Mehta",
        "Mehta",
        "K. Iyer",
        "Iyer",
        "City Clinic",
    ]


def test_keyterms_deduplicate_and_respect_the_limit():
    many = [f"Dr. Name{i} Sharma" for i in range(80)]
    terms = keyterms_for(many, "C")
    assert terms.count("Sharma") == 1 and len(terms) <= 100


async def test_clinic_keyterms_loads_from_the_tools_package(monkeypatch):
    """Regression: the tools.py -> tools/ split left a stale `tools.CLINIC_NAME`, so keyterms
    silently failed open on every call."""
    from types import SimpleNamespace

    from agent import tools, vocabulary

    async def fake_doctors():
        return [SimpleNamespace(full_name="Dr. Anil Sharma")]

    monkeypatch.setattr(tools, "load_doctors", fake_doctors)
    terms = await vocabulary.clinic_keyterms()
    assert any("Sharma" in t for t in terms)
