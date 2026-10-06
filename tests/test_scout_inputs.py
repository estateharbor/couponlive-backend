"""Data-only inputs the scheduled offer-scout writes (no code changes needed):
merchant_home for new stores, a DB-free --check, and trials CSVs."""
from __future__ import annotations

from pathlib import Path

from models.enums import TrialOfferType
from scheduler import import_editorial as ie
from scheduler import seed_trials as st

HEADER = ("store,category,code,headline,description,how_to_redeem,expiry,terms,"
          "verified_note,source_url,code_type,slug_suggestion,merchant_home\n")


def _csv(tmp_path: Path, body: str, name="x.csv") -> Path:
    p = tmp_path / name
    p.write_text(HEADER + body, encoding="utf-8")
    return p


def test_merchant_home_used_for_new_store_but_never_an_aggregator():
    row = {"store": "Brand New Store", "merchant_home": "https://www.brandnew.in"}
    assert ie._merchant_home(row) == "https://www.brandnew.in"
    assert ie._merchant_home({"merchant_home": "https://www.grabon.in/x"}) is None
    assert ie._merchant_home({"merchant_home": "http://insecure.in"}) is None
    assert ie._merchant_home({"merchant_home": "https://x.in/?ref=aff"}) is None


def test_check_flags_missing_clickout_and_past_end_date(tmp_path, capsys):
    ok = _csv(tmp_path, "Brand New Store,shopping,NEW10,10% off,d,h,31 Dec 2099,t,v,"
                        "https://www.brandnew.in/offers,code,brandnew-new10,https://www.brandnew.in\n")
    assert ie.check(ok) == 0
    bad = _csv(tmp_path, "Unknown Store,shopping,X5,5% off,d,h,1 Jan 2020,t,v,u,code,unk-x5,\n",
               name="bad.csv")
    assert ie.check(bad) == 1
    out = capsys.readouterr().out
    assert "no click-out URL" in out and "end date already passed" in out


TRIAL_HEADER = ("name,slug,vendor,category,ai,website,pricing,offer_type,title,trial_days,"
                "card_required,india_available,eligibility,renew_inr,renew_usd,renew_period,"
                "credit_amount,credit_currency,signup_url\n")


def test_trial_csv_parses_and_keeps_existing_tool_fields(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text(TRIAL_HEADER +
        "WRONG NAME,vercel,,,,https://evil.example,,startup_credit,Vercel — new perk,,"
        "false,,Startups,,,,5000,USD,https://vercel.com/startups\n"
        "Newtool,newtool,NT,ai,true,https://newtool.ai,,lifetime_free_tier,Newtool free plan,,"
        "false,true,,,,,,,https://newtool.ai/signup\n"
        "Bad,bad,,,,,,not_a_type,X,,,,,,,,,,https://bad.ai\n", encoding="utf-8")
    rows, problems = st.load_trial_csv(p, list(st.SEED))
    assert len(rows) == 2 and len(problems) == 1 and "offer_type" in problems[0]
    vercel = next(r for r in rows if r["slug"] == "vercel")
    assert vercel["name"] == "Vercel" and vercel["website"] == "https://vercel.com"
    assert vercel["credit_amount"] == 5000.0 and vercel["card_required"] is False
    nt = next(r for r in rows if r["slug"] == "newtool")
    assert nt["offer_type"] is TrialOfferType.lifetime_free_tier and nt["ai"] is True


def test_trial_ends_column_sets_end_date(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text(TRIAL_HEADER.rstrip("\n") + ",ends\n" +
        "Newtool,newtool,NT,ai,true,https://newtool.ai,,card_trial,Newtool $1 first month,,"
        "true,,,,,,,,https://newtool.ai/pricing,18 Oct 2026\n", encoding="utf-8")
    rows, problems = st.load_trial_csv(p, list(st.SEED))
    assert not problems
    assert rows[0]["expires_at"] is not None and rows[0]["expires_at"].day == 18
