"""Idempotent seed for the Free Trials vertical.

A curated starter set of popular tools + one representative offer each, so the
tab has honest content before the LLM-extraction/verification pipeline (T2/T3)
takes over. Facts are best-effort and land as `verification_status=unverified`
(shown as "Not verified yet") — never as "verified". Unknown facts are left NULL
and must render as "Unknown". Re-running updates rows in place (keyed by slug /
tool+title), so it's safe to run repeatedly.

    docker compose exec worker python -m scheduler.seed_trials
"""
from __future__ import annotations

from sqlalchemy import select

from core.logging import get_logger
from models.base import get_sessionmaker
from models.enums import TrialOfferType, TrialStatus, TrialVerificationStatus
from models.models import Tool, TrialOffer

log = get_logger("seed.trials")

T = TrialOfferType

# name, slug, vendor, category, ai, website, offer_type, title, trial_days,
# card_required, india_available, eligibility, renew_inr, renew_usd, renew_period,
# credit_amount, credit_currency, signup_url
SEED: list[dict] = [
    # --- AI ---
    dict(name="ChatGPT", slug="chatgpt", vendor="OpenAI", category="ai", ai=True,
         website="https://chat.openai.com", offer_type=T.lifetime_free_tier,
         title="ChatGPT free plan — no card needed", card_required=False,
         india_available=True, signup_url="https://chat.openai.com/"),
    dict(name="Claude", slug="claude", vendor="Anthropic", category="ai", ai=True,
         website="https://claude.ai", offer_type=T.lifetime_free_tier,
         title="Claude free plan — no card needed", card_required=False,
         india_available=True, signup_url="https://claude.ai/"),
    dict(name="Perplexity", slug="perplexity", vendor="Perplexity AI", category="ai", ai=True,
         website="https://perplexity.ai", offer_type=T.freemium_premium_trial,
         title="Perplexity free plan (Pro often free via Airtel)", card_required=False,
         india_available=True, signup_url="https://www.perplexity.ai/"),
    dict(name="Notion AI", slug="notion-ai", vendor="Notion", category="ai", ai=True,
         website="https://www.notion.so/product/ai", offer_type=T.freemium_premium_trial,
         title="Notion AI — limited free trial responses", card_required=False,
         signup_url="https://www.notion.so/product/ai"),
    dict(name="ElevenLabs", slug="elevenlabs", vendor="ElevenLabs", category="ai", ai=True,
         website="https://elevenlabs.io", offer_type=T.no_card_trial,
         title="ElevenLabs free tier — no card", card_required=False,
         signup_url="https://elevenlabs.io/"),
    dict(name="Gamma", slug="gamma", vendor="Gamma", category="ai", ai=True,
         website="https://gamma.app", offer_type=T.no_card_trial,
         title="Gamma free plan with AI credits — no card", card_required=False,
         signup_url="https://gamma.app/"),
    dict(name="Cursor", slug="cursor", vendor="Anysphere", category="ai", ai=True,
         website="https://cursor.com", offer_type=T.freemium_premium_trial,
         title="Cursor Pro trial", trial_days=14, card_required=None,
         signup_url="https://www.cursor.com/"),
    dict(name="GitHub Copilot", slug="github-copilot", vendor="GitHub", category="ai", ai=True,
         website="https://github.com/features/copilot", offer_type=T.card_trial,
         title="GitHub Copilot — 30-day free trial", trial_days=30, card_required=True,
         renew_usd=10, renew_period="month", signup_url="https://github.com/features/copilot"),

    # --- Design & creative ---
    dict(name="Canva Pro", slug="canva-pro", vendor="Canva", category="design", ai=False,
         website="https://www.canva.com", offer_type=T.card_trial,
         title="Canva Pro — 30-day free trial", trial_days=30, card_required=True,
         india_available=True, renew_inr=499, renew_period="month",
         signup_url="https://www.canva.com/pro/"),
    dict(name="Figma", slug="figma", vendor="Figma", category="design", ai=False,
         website="https://figma.com", offer_type=T.lifetime_free_tier,
         title="Figma Starter — free forever, no card", card_required=False,
         signup_url="https://www.figma.com/"),
    dict(name="Adobe Creative Cloud", slug="adobe-creative-cloud", vendor="Adobe",
         category="design", ai=False, website="https://adobe.com",
         offer_type=T.card_trial, title="Adobe Creative Cloud — 7-day free trial",
         trial_days=7, card_required=True, signup_url="https://www.adobe.com/creativecloud.html"),
    dict(name="Framer", slug="framer", vendor="Framer", category="design", ai=False,
         website="https://framer.com", offer_type=T.lifetime_free_tier,
         title="Framer free plan — no card", card_required=False,
         signup_url="https://www.framer.com/"),

    # --- Productivity ---
    dict(name="Notion", slug="notion", vendor="Notion", category="productivity", ai=False,
         website="https://notion.so", offer_type=T.lifetime_free_tier,
         title="Notion free plan — no card", card_required=False,
         signup_url="https://www.notion.so/"),
    dict(name="Grammarly", slug="grammarly", vendor="Grammarly", category="productivity", ai=True,
         website="https://grammarly.com", offer_type=T.lifetime_free_tier,
         title="Grammarly free plan — no card", card_required=False,
         signup_url="https://www.grammarly.com/"),
    dict(name="ClickUp", slug="clickup", vendor="ClickUp", category="productivity", ai=False,
         website="https://clickup.com", offer_type=T.lifetime_free_tier,
         title="ClickUp Free Forever — no card", card_required=False,
         signup_url="https://clickup.com/"),
    dict(name="Todoist", slug="todoist", vendor="Doist", category="productivity", ai=False,
         website="https://todoist.com", offer_type=T.lifetime_free_tier,
         title="Todoist free plan — no card", card_required=False,
         signup_url="https://www.todoist.com/"),
    dict(name="Zoom", slug="zoom", vendor="Zoom", category="productivity", ai=False,
         website="https://zoom.us", offer_type=T.lifetime_free_tier,
         title="Zoom Basic — free, no card", card_required=False,
         signup_url="https://zoom.us/"),

    # --- Developer & cloud ---
    dict(name="GitHub Student Developer Pack", slug="github-student-pack", vendor="GitHub",
         category="developer-cloud", ai=False, website="https://education.github.com/pack",
         offer_type=T.student_offer, title="GitHub Student Pack — free tools for students",
         card_required=False, eligibility="Verified students",
         signup_url="https://education.github.com/pack"),
    dict(name="AWS Activate", slug="aws-activate", vendor="Amazon Web Services",
         category="developer-cloud", ai=False, website="https://aws.amazon.com/activate/",
         offer_type=T.startup_credit, title="AWS Activate — up to $100,000 in credits",
         card_required=False, eligibility="Eligible startups", credit_amount=100000,
         credit_currency="USD", signup_url="https://aws.amazon.com/activate/"),
    dict(name="Google Cloud for Startups", slug="google-cloud-startups", vendor="Google",
         category="developer-cloud", ai=False, website="https://cloud.google.com/startup",
         offer_type=T.startup_credit, title="Google for Startups — cloud credits",
         card_required=False, eligibility="Eligible startups",
         signup_url="https://cloud.google.com/startup"),
    dict(name="Microsoft for Startups", slug="microsoft-for-startups", vendor="Microsoft",
         category="developer-cloud", ai=False, website="https://www.microsoft.com/startups",
         offer_type=T.startup_credit, title="Microsoft for Startups — Azure credits",
         card_required=False, eligibility="Eligible startups",
         signup_url="https://www.microsoft.com/en-us/startups"),
    dict(name="Vercel", slug="vercel", vendor="Vercel", category="developer-cloud", ai=False,
         website="https://vercel.com", offer_type=T.lifetime_free_tier,
         title="Vercel Hobby — free forever, no card", card_required=False,
         signup_url="https://vercel.com/"),
    dict(name="Supabase", slug="supabase", vendor="Supabase", category="developer-cloud", ai=False,
         website="https://supabase.com", offer_type=T.lifetime_free_tier,
         title="Supabase Free tier — no card", card_required=False,
         signup_url="https://supabase.com/"),
    dict(name="MongoDB Atlas", slug="mongodb-atlas", vendor="MongoDB", category="developer-cloud",
         ai=False, website="https://www.mongodb.com/atlas", offer_type=T.lifetime_free_tier,
         title="MongoDB Atlas M0 — free forever, no card", card_required=False,
         signup_url="https://www.mongodb.com/cloud/atlas/register"),
    dict(name="DigitalOcean", slug="digitalocean", vendor="DigitalOcean", category="developer-cloud",
         ai=False, website="https://www.digitalocean.com", offer_type=T.ai_credits,
         title="DigitalOcean — $200 signup credit (60 days)", card_required=True,
         credit_amount=200, credit_currency="USD",
         signup_url="https://www.digitalocean.com/"),
    dict(name="JetBrains", slug="jetbrains", vendor="JetBrains", category="developer-cloud", ai=False,
         website="https://www.jetbrains.com", offer_type=T.student_offer,
         title="JetBrains — free for students (+30-day trial)", trial_days=30,
         card_required=False, eligibility="Students / 30-day trial for all",
         signup_url="https://www.jetbrains.com/community/education/"),

    # --- Marketing & SEO ---
    dict(name="Semrush", slug="semrush", vendor="Semrush", category="marketing-seo", ai=False,
         website="https://www.semrush.com", offer_type=T.card_trial,
         title="Semrush Pro — 7-day free trial", trial_days=7, card_required=True,
         renew_usd=139.95, renew_period="month", signup_url="https://www.semrush.com/"),
    dict(name="Mailchimp", slug="mailchimp", vendor="Intuit Mailchimp", category="marketing-seo",
         ai=False, website="https://mailchimp.com", offer_type=T.lifetime_free_tier,
         title="Mailchimp free plan — no card", card_required=False,
         signup_url="https://mailchimp.com/"),
    dict(name="HubSpot", slug="hubspot", vendor="HubSpot", category="sales-crm", ai=False,
         website="https://www.hubspot.com", offer_type=T.lifetime_free_tier,
         title="HubSpot free CRM — no card", card_required=False,
         signup_url="https://www.hubspot.com/products/crm"),

    # --- Learning ---
    dict(name="Coursera", slug="coursera", vendor="Coursera", category="learning", ai=False,
         website="https://coursera.org", offer_type=T.card_trial,
         title="Coursera Plus — 7-day free trial", trial_days=7, card_required=True,
         india_available=True, signup_url="https://www.coursera.org/courseraplus"),
    dict(name="LinkedIn Learning", slug="linkedin-learning", vendor="LinkedIn", category="learning",
         ai=False, website="https://www.linkedin.com/learning", offer_type=T.card_trial,
         title="LinkedIn Learning — 1-month free trial", trial_days=30, card_required=True,
         signup_url="https://www.linkedin.com/learning/"),

    # --- Security & VPN ---
    dict(name="Proton", slug="proton", vendor="Proton", category="security-vpn", ai=False,
         website="https://proton.me", offer_type=T.lifetime_free_tier,
         title="Proton Free (Mail/VPN) — no card", card_required=False,
         signup_url="https://proton.me/"),
    dict(name="Bitwarden", slug="bitwarden", vendor="Bitwarden", category="security-vpn", ai=False,
         website="https://bitwarden.com", offer_type=T.lifetime_free_tier,
         title="Bitwarden free plan — no card", card_required=False,
         signup_url="https://bitwarden.com/"),

    # --- OTT & telecom (India) ---
    dict(name="Netflix", slug="netflix", vendor="Netflix", category="ott-telecom", ai=False,
         website="https://netflix.com", offer_type=T.card_trial,
         title="Netflix — check current India plans (mobile from ₹149)", card_required=True,
         india_available=True, renew_inr=149, renew_period="month",
         signup_url="https://www.netflix.com/in/"),
    dict(name="Amazon Prime Video", slug="amazon-prime-video", vendor="Amazon", category="ott-telecom",
         ai=False, website="https://www.primevideo.com", offer_type=T.card_trial,
         title="Amazon Prime — 30-day free trial", trial_days=30, card_required=True,
         india_available=True, signup_url="https://www.amazon.in/prime"),
]


def main() -> None:
    session = get_sessionmaker()()
    created_tools = created_offers = updated = 0
    try:
        for row in SEED:
            tool = session.scalar(select(Tool).where(Tool.slug == row["slug"]))
            if tool is None:
                tool = Tool(slug=row["slug"])
                session.add(tool)
                created_tools += 1
            tool.name = row["name"]
            tool.vendor_name = row.get("vendor")
            tool.category = row.get("category")
            tool.is_ai_tool = bool(row.get("ai"))
            tool.website_url = row.get("website")
            tool.pricing_page_url = row.get("pricing") or row.get("website")
            tool.status = TrialStatus.live
            session.flush()

            offer = session.scalar(
                select(TrialOffer).where(
                    TrialOffer.tool_id == tool.id, TrialOffer.title == row["title"]
                )
            )
            if offer is None:
                offer = TrialOffer(tool_id=tool.id, title=row["title"])
                session.add(offer)
                created_offers += 1
            else:
                updated += 1
            offer.offer_type = row.get("offer_type", TrialOfferType.unknown)
            offer.trial_days = row.get("trial_days")
            offer.card_required = row.get("card_required")
            offer.india_available = row.get("india_available")
            offer.eligibility = row.get("eligibility")
            offer.renew_price_inr = row.get("renew_inr")
            offer.renew_price_usd = row.get("renew_usd")
            offer.renew_period = row.get("renew_period")
            offer.credit_amount = row.get("credit_amount")
            offer.credit_currency = row.get("credit_currency")
            offer.signup_url = row["signup_url"]
            offer.source = "manual"
            offer.source_url = row.get("website")
            # Curated, NOT yet live-checked — stays unverified until T2/T3 confirm.
            offer.verification_status = TrialVerificationStatus.unverified
            offer.status = TrialStatus.live
        session.commit()
        print(f"seed done — tools created: {created_tools}, offers created: {created_offers}, "
              f"offers updated: {updated}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
