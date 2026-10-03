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
from models.models import Tool, TrialOffer, TrialReminder

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
         title="Perplexity — free plan (limited Pro searches/day)", card_required=False,
         india_available=True, signup_url="https://www.perplexity.ai/"),
    dict(name="Notion AI", slug="notion-ai", vendor="Notion", category="ai", ai=True,
         website="https://www.notion.so/product/ai", offer_type=T.freemium_premium_trial,
         title="Notion AI — limited free trial responses", card_required=False,
         signup_url="https://www.notion.so/product/ai"),
    dict(name="ElevenLabs", slug="elevenlabs", vendor="ElevenLabs", category="ai", ai=True,
         website="https://elevenlabs.io", offer_type=T.lifetime_free_tier,
         title="ElevenLabs — 10,000 free characters/month", card_required=False,
         india_available=True, signup_url="https://elevenlabs.io/"),
    dict(name="Gamma", slug="gamma", vendor="Gamma", category="ai", ai=True,
         website="https://gamma.app", offer_type=T.no_card_trial,
         title="Gamma free plan with AI credits — no card", card_required=False,
         signup_url="https://gamma.app/"),
    dict(name="Cursor", slug="cursor", vendor="Anysphere", category="ai", ai=True,
         website="https://cursor.com", offer_type=T.lifetime_free_tier,
         title="Cursor — free Hobby plan (limited Tab)", card_required=False,
         signup_url="https://www.cursor.com/"),
    dict(name="GitHub Copilot", slug="github-copilot", vendor="GitHub", category="ai", ai=True,
         website="https://github.com/features/copilot", offer_type=T.lifetime_free_tier,
         title="GitHub Copilot Free — 2,000 completions + 50 chat/mo", card_required=False,
         india_available=True, signup_url="https://github.com/features/copilot/plans"),

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

    # --- AI (editorial cross-check, Oct 2026) -----------------------------
    # Google AI Pro — one tool, several offers (consumer trial / Jio / student).
    dict(name="Google AI Pro", slug="google-ai-pro", vendor="Google", category="ai", ai=True,
         website="https://gemini.google.com", offer_type=T.card_trial,
         title="Google AI Pro — 1-month free trial", trial_days=30, card_required=True,
         india_available=True, renew_usd=19.99, renew_period="month",
         signup_url="https://gemini.google.com/"),
    dict(name="Google AI Pro", slug="google-ai-pro", vendor="Google", category="ai", ai=True,
         website="https://gemini.google.com", offer_type=T.telecom_bundle,
         title="Google AI Pro — up to 18 months via eligible Jio plans", card_required=None,
         india_available=True, eligibility="Eligible Jio plans",
         signup_url="https://gemini.google.com/"),
    dict(name="Google AI Pro", slug="google-ai-pro", vendor="Google", category="ai", ai=True,
         website="https://gemini.google.com", offer_type=T.student_offer,
         title="Google AI Plus — 12 months free for students", card_required=True,
         india_available=True, eligibility="Verified college students (India)",
         signup_url="https://one.google.com/ai-student"),
    dict(name="Google Gemini", slug="google-gemini", vendor="Google", category="ai", ai=True,
         website="https://gemini.google.com", offer_type=T.lifetime_free_tier,
         title="Gemini — free tier, no card", card_required=False, india_available=True,
         signup_url="https://gemini.google.com/"),
    dict(name="Microsoft 365 Premium", slug="microsoft-365-premium", vendor="Microsoft",
         category="productivity", ai=True, website="https://www.microsoft.com/microsoft-365",
         offer_type=T.card_trial, title="Microsoft 365 Premium — 1-month free trial (incl. Copilot)",
         trial_days=30, card_required=True, india_available=True, renew_usd=19.99,
         renew_period="month", signup_url="https://www.microsoft.com/microsoft-365"),
    dict(name="DeepSeek", slug="deepseek", vendor="DeepSeek", category="ai", ai=True,
         website="https://chat.deepseek.com", offer_type=T.lifetime_free_tier,
         title="DeepSeek Chat — free to use, no card", card_required=False,
         india_available=True, signup_url="https://chat.deepseek.com/"),
    dict(name="Runway", slug="runway", vendor="Runway", category="ai", ai=True,
         website="https://runwayml.com", offer_type=T.ai_credits,
         title="Runway — 125 one-time free credits", card_required=False,
         india_available=True, credit_amount=125, signup_url="https://runwayml.com/"),
    dict(name="Adobe Firefly", slug="adobe-firefly", vendor="Adobe", category="ai", ai=True,
         website="https://www.adobe.com/products/firefly.html", offer_type=T.lifetime_free_tier,
         title="Adobe Firefly — free tier with monthly generative credits", card_required=False,
         india_available=True, signup_url="https://www.adobe.com/products/firefly.html"),
    dict(name="Anthropic API", slug="anthropic-api", vendor="Anthropic", category="developer-cloud",
         ai=True, website="https://platform.claude.com", offer_type=T.ai_credits,
         title="Anthropic API — $5 free starter credit", card_required=False,
         credit_amount=5, credit_currency="USD", signup_url="https://platform.claude.com/"),
    dict(name="Otter.ai", slug="otter-ai", vendor="Otter.ai", category="productivity", ai=True,
         website="https://otter.ai", offer_type=T.card_trial,
         title="Otter.ai Business — 7-day free trial", trial_days=7, card_required=True,
         signup_url="https://otter.ai/"),

    # --- Developer & cloud (editorial cross-check, 2 Oct 2026) ------------
    dict(name="Firecrawl", slug="firecrawl", vendor="Firecrawl", category="developer-cloud",
         ai=True, website="https://www.firecrawl.dev", pricing="https://www.firecrawl.dev/pricing",
         offer_type=T.lifetime_free_tier,
         title="Firecrawl Free — 1,000 credits/month, no card", card_required=False,
         credit_amount=1000, signup_url="https://www.firecrawl.dev/pricing"),
    dict(name="Firecrawl", slug="firecrawl", vendor="Firecrawl", category="developer-cloud",
         ai=True, website="https://www.firecrawl.dev", pricing="https://www.firecrawl.dev/pricing",
         offer_type=T.student_offer,
         title="Firecrawl — 10,000 free credits for students (code STUDENTEDU)",
         card_required=False, credit_amount=10000,
         eligibility="Enrolled students with an academic email; non-commercial use",
         signup_url="https://www.firecrawl.dev/student-program"),
    dict(name="Railway", slug="railway", vendor="Railway", category="developer-cloud", ai=False,
         website="https://railway.com", pricing="https://railway.com/pricing",
         offer_type=T.no_card_trial,
         title="Railway — $5 free trial credit (up to 30 days)", trial_days=30,
         card_required=False, credit_amount=5, credit_currency="USD",
         eligibility="New users; then $1/month free plan",
         signup_url="https://docs.railway.com/pricing/free-trial"),
    dict(name="Neon", slug="neon", vendor="Neon (Databricks)", category="developer-cloud", ai=False,
         website="https://neon.com", offer_type=T.startup_credit,
         title="Neon Startups — up to $1,000 credits (up to $200K with Databricks)",
         card_required=False, credit_amount=1000, credit_currency="USD",
         eligibility="Self-funded (<$1M) up to $1,000; venture-backed/accelerator up to $200K Neon+Databricks; 12 months",
         signup_url="https://neon.com/startups"),
    dict(name="Notion", slug="notion", vendor="Notion", category="productivity", ai=False,
         website="https://notion.so", offer_type=T.student_offer,
         title="Notion Education Plus — free for students & educators", card_required=False,
         eligibility="University students/educators with a school email (no K-12); once per email",
         signup_url="https://www.notion.com/help/notion-for-education"),
    dict(name="Runway", slug="runway", vendor="Runway", category="ai", ai=True,
         website="https://runwayml.com", offer_type=T.student_offer,
         title="Runway — 25% off paid plans for students & educators", card_required=True,
         eligibility="Students/educators verified via SheerID",
         signup_url="https://runway.com/educators"),
    dict(name="Vercel", slug="vercel", vendor="Vercel", category="developer-cloud", ai=False,
         website="https://vercel.com", offer_type=T.startup_credit,
         title="Vercel for Startups — up to $30,000 credits", card_required=True,
         credit_amount=30000, credit_currency="USD",
         eligibility="Backed by an approved Vercel partner; Series A or earlier; apply within 12 months of last round",
         signup_url="https://vercel.com/startups/credits"),
    dict(name="PostHog", slug="posthog", vendor="PostHog", category="developer-cloud", ai=False,
         website="https://posthog.com", offer_type=T.startup_credit,
         title="PostHog for Startups — $50,000 credits for 12 months", card_required=None,
         credit_amount=50000, credit_currency="USD",
         eligibility="Early-stage startups with a PostHog account",
         signup_url="https://posthog.com/startups"),
    dict(name="Cloudflare for Startups", slug="cloudflare-startups", vendor="Cloudflare",
         category="developer-cloud", ai=False, website="https://www.cloudflare.com/startups/",
         offer_type=T.startup_credit,
         title="Cloudflare for Startups — $10K / $100K / $350K credits", card_required=None,
         credit_amount=10000, credit_currency="USD",
         eligibility="Founded <10 yrs, up to Series B; $10K self-funded (<$1M), higher tiers via partner investors",
         signup_url="https://www.cloudflare.com/startups/"),

    # --- Startup & student programs (editorial cross-check, 3 Oct 2026) ---
    dict(name="Linear", slug="linear", vendor="Linear", category="productivity", ai=False,
         website="https://linear.app", offer_type=T.startup_credit,
         title="Linear for Startups — free Business plan via a Linear partner",
         card_required=False,
         eligibility="Via an official Linear partner (VC/accelerator/community); <50 employees; non-paying users",
         signup_url="https://linear.app/startups"),
    dict(name="Anthropic API", slug="anthropic-api", vendor="Anthropic", category="developer-cloud",
         ai=True, website="https://platform.claude.com", offer_type=T.startup_credit,
         title="Claude for Startups — free API credits + priority rate limits",
         card_required=False,
         eligibility="Institutional equity funding; founded <4 yrs; first-party Claude API (Console) only",
         signup_url="https://claude.com/programs/startups"),
    dict(name="Firecrawl", slug="firecrawl", vendor="Firecrawl", category="developer-cloud",
         ai=True, website="https://www.firecrawl.dev", pricing="https://www.firecrawl.dev/pricing",
         offer_type=T.startup_credit,
         title="Firecrawl for Startups — 2× monthly credits for a year (annual plans)",
         card_required=True,
         eligibility="<$10M raised, <50 people, founded <5 yrs; extra credits capped $2.5K/mo ($30K/yr)",
         signup_url="https://www.firecrawl.dev/startups"),
    dict(name="Devin", slug="devin", vendor="Cognition", category="ai", ai=True,
         website="https://devin.ai", offer_type=T.startup_credit,
         title="Devin for Startups — $15K credits + up to $50K matching grants",
         card_required=None, credit_amount=15000, credit_currency="USD",
         eligibility="Early-stage startups (application review); unlimited seats",
         signup_url="https://devin.ai/startups"),
    dict(name="Kiro", slug="kiro", vendor="AWS", category="ai", ai=True,
         website="https://kiro.dev", offer_type=T.startup_credit,
         title="Kiro for Startups — up to 1 year of Kiro Pro+ credits",
         card_required=None,
         eligibility="VC-backed, early-stage to Series A; not enrolled in AWS Activate; some countries excluded",
         signup_url="https://kiro.dev/startups/"),
    dict(name="Render", slug="render", vendor="Render", category="developer-cloud", ai=False,
         website="https://render.com", offer_type=T.startup_credit,
         title="Render for Startups — $500 to $100K credits (1 year)",
         card_required=None, credit_amount=500, credit_currency="USD",
         eligibility="New customer, seed ≥$25K, Series A or earlier; higher tiers ($2.5K–$100K) via partner VCs",
         signup_url="https://render.com/startups"),
    dict(name="LangSmith", slug="langsmith", vendor="LangChain", category="ai", ai=True,
         website="https://www.langchain.com/langsmith", offer_type=T.startup_credit,
         title="LangSmith for Startups — $10K credits for 1 year (Scale tier)",
         card_required=None, credit_amount=10000, credit_currency="USD",
         eligibility="Backed by a LangChain premier VC partner; Series A or earlier; YC gets 2 years",
         signup_url="https://www.langchain.com/startups"),
    dict(name="Auth0", slug="auth0", vendor="Okta", category="developer-cloud", ai=False,
         website="https://auth0.com", offer_type=T.startup_credit,
         title="Auth0 for Startups — 1 year B2B Professional free (100K MAUs)",
         card_required=None,
         eligibility="VC-backed, <$5M raised, <$1M ARR, <2 yrs since incorporation; not existing paid customers",
         signup_url="https://auth0.com/startups"),
    dict(name="Clerk", slug="clerk", vendor="Clerk", category="developer-cloud", ai=False,
         website="https://clerk.com", offer_type=T.startup_credit,
         title="Clerk for YC — 1 year Clerk Pro + add-ons free (YC F26)",
         card_required=None,
         eligibility="YC F26 batch (other YC batches: 50% off for 1 year); until $5M raised",
         signup_url="https://clerk.com/startups/yc"),
    dict(name="Resend", slug="resend", vendor="Resend", category="developer-cloud", ai=False,
         website="https://resend.com", offer_type=T.startup_credit,
         title="Resend for YC — $25K credits for 12 months",
         card_required=None, credit_amount=25000, credit_currency="USD",
         eligibility="Current YC batch, sign up before Demo Day ($15K for YC alumni)",
         signup_url="https://resend.com/yc"),
    dict(name="Framer", slug="framer", vendor="Framer", category="design", ai=False,
         website="https://framer.com", offer_type=T.startup_credit,
         title="Framer for Startups — 1 year of Framer Pro free (~$360)",
         card_required=None, eligibility="Startups accepted into the Framer startups program",
         signup_url="https://www.framer.com/startups/"),
    dict(name="MojoAuth", slug="mojoauth", vendor="MojoAuth", category="developer-cloud", ai=False,
         website="https://mojoauth.com", offer_type=T.startup_credit,
         title="MojoAuth — 50% off annual Business Pro for startups & nonprofits",
         card_required=True,
         eligibility="Founded <5 yrs, ≤$10M raised, or verified nonprofit; new customers only",
         signup_url="https://mojoauth.com/startup/"),
    dict(name="Stripe Atlas", slug="stripe-atlas", vendor="Stripe", category="developer-cloud",
         ai=False, website="https://stripe.com/atlas", offer_type=T.startup_credit,
         title="Stripe Atlas — $2,500 Stripe credits + $50K+ partner discounts",
         card_required=True, credit_amount=2500, credit_currency="USD",
         eligibility="Companies incorporating with Stripe Atlas ($500 one-time fee)",
         signup_url="https://stripe.com/atlas"),
    dict(name="HubSpot", slug="hubspot", vendor="HubSpot", category="sales-crm", ai=False,
         website="https://www.hubspot.com", offer_type=T.startup_credit,
         title="HubSpot for Startups — up to 90% off Pro/Enterprise in year 1",
         card_required=True,
         eligibility="Pre-seed to Series A via partner (90% yr 1); approved entrepreneur orgs get 30%; annual commitment",
         signup_url="https://www.hubspot.com/startups"),
    dict(name="MongoDB Atlas", slug="mongodb-atlas", vendor="MongoDB", category="developer-cloud",
         ai=False, website="https://www.mongodb.com/atlas", offer_type=T.startup_credit,
         title="MongoDB for Startups — Atlas credits + Voyage AI tokens",
         card_required=None,
         eligibility="<7 yrs old, Series A or earlier, single software product; amount set on acceptance; 12 months",
         signup_url="https://www.mongodb.com/startups"),
]

# Offers that earlier seeds created but are now STALE/incorrect (promo ended or
# product changed). Removed by (slug, exact old title) so corrected versions
# above don't leave a duplicate behind. A verified offer is never listed here.
RETIRE: list[tuple[str, str]] = [
    ("perplexity", "Perplexity free plan (Pro often free via Airtel)"),  # Airtel Pro ended Jan 2026
    ("cursor", "Cursor Pro trial"),                                       # Pro 14-day trial removed 2026
    ("github-copilot", "GitHub Copilot — 30-day free trial"),             # replaced by free plan
    ("elevenlabs", "ElevenLabs free tier — no card"),                    # replaced by char-quota title
]


def _offer_rank(o: TrialOffer) -> tuple:
    """Higher is better: verified first, then most recently verified, then oldest id."""
    verified = o.verification_status == TrialVerificationStatus.verified
    ts = o.last_verified_at.timestamp() if o.last_verified_at else 0.0
    return (verified, ts, -o.id)


def dedupe_offers(session) -> int:
    """Collapse offers that share a tool + title (case/space-insensitive) into one,
    keeping the best-verified copy and moving any reminders onto it. Returns the
    number of duplicate rows removed."""
    groups: dict[tuple[int, str], list[TrialOffer]] = {}
    for o in session.scalars(select(TrialOffer)):
        key = (o.tool_id, " ".join((o.title or "").lower().split()))
        groups.setdefault(key, []).append(o)
    removed = 0
    for offers in groups.values():
        if len(offers) < 2:
            continue
        offers.sort(key=_offer_rank, reverse=True)
        keep, dupes = offers[0], offers[1:]
        for d in dupes:
            for r in session.scalars(select(TrialReminder).where(TrialReminder.offer_id == d.id)):
                r.offer_id = keep.id
            session.delete(d)
            removed += 1
    session.flush()
    return removed


def main() -> None:
    session = get_sessionmaker()()
    created_tools = created_offers = updated = retired = 0
    try:
        # Remove offers that are now stale/incorrect (keyed by slug + old title),
        # so corrected versions below don't leave a duplicate behind.
        for slug, title in RETIRE:
            tool = session.scalar(select(Tool).where(Tool.slug == slug))
            if tool is None:
                continue
            stale = session.scalar(
                select(TrialOffer).where(
                    TrialOffer.tool_id == tool.id, TrialOffer.title == title
                )
            )
            if stale is not None:
                session.delete(stale)
                retired += 1
        session.flush()
        deduped = dedupe_offers(session)

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
              f"offers updated: {updated}, stale offers retired: {retired}, "
              f"duplicates removed: {deduped}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
