"""The background skill curator: merge duplicates, retire losers."""
from koretex_agent import skills as sk
from koretex_agent.schemas import Skill


def _seed(catalog, name, description, wins=0, losses=0):
    sk.save_skill(Skill(name=name, description=description, body="steps"), catalog=catalog)
    for _ in range(wins):
        sk.record_outcome([name], won=True, catalog=catalog)
    for _ in range(losses):
        sk.record_outcome([name], won=False, catalog=catalog)


def test_curate_retires_proven_losers(tmp_path):
    # distinct descriptions so they are not merged, only judged on record
    _seed(tmp_path, "flaky-skill", "when parsing XML configuration files", wins=1, losses=4)  # 20% / 5
    _seed(tmp_path, "good-skill", "when rendering charts from tabular data", wins=4, losses=1)  # 80%
    report = sk.curate(tmp_path, min_uses=3)
    names = [s["name"] for s in sk.catalog_index(tmp_path)]
    assert "flaky-skill" not in names and "good-skill" in names
    assert report["retired"] == [{"name": "flaky-skill", "record": "1W/4L"}]
    # retired skill moved out of selection but preserved for audit
    assert (tmp_path / "_retired" / "flaky-skill" / "SKILL.md").exists()
    assert sk.load_ledger(tmp_path)["flaky-skill"]["retired"] is True


def test_curate_spares_losers_below_min_uses(tmp_path):
    _seed(tmp_path, "new-skill", "when doing Z", wins=0, losses=1)  # only 1 use
    sk.curate(tmp_path, min_uses=3)
    assert "new-skill" in [s["name"] for s in sk.catalog_index(tmp_path)]  # too early to judge


def test_curate_merges_duplicates_keeping_better_record(tmp_path):
    _seed(tmp_path, "csv-json-a", "when converting CSV files to JSON output", wins=1, losses=0)
    _seed(tmp_path, "csv-json-b", "when converting CSV files to JSON output", wins=3, losses=0)
    report = sk.curate(tmp_path, dup_threshold=0.6)
    names = [s["name"] for s in sk.catalog_index(tmp_path)]
    assert names == ["csv-json-b"]  # higher-win survivor kept, duplicate merged away
    assert report["merged"] == [{"from": "csv-json-a", "into": "csv-json-b"}]
    # loser's stats folded into the survivor
    assert sk.load_ledger(tmp_path)["csv-json-b"]["wins"] == 4


def test_curate_leaves_distinct_skills_alone(tmp_path):
    _seed(tmp_path, "csv-json", "when converting CSV to JSON")
    _seed(tmp_path, "web-scrape", "when scraping HTML web pages")
    sk.curate(tmp_path)
    assert {s["name"] for s in sk.catalog_index(tmp_path)} == {"csv-json", "web-scrape"}


def test_curate_report_shape_on_empty_catalog(tmp_path):
    report = sk.curate(tmp_path)
    assert report == {"merged": [], "retired": [], "kept": []}
