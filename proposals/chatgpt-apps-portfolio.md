# Proposal: A Profitable Portfolio of ChatGPT Apps for realitymarketing.ai

**Prepared for:** Ricardo, COO, realitymarketing.ai
**Date:** 2026-10-04
**Status:** Draft for decision

---

## 1. Executive summary

"ChatGPT plugins" are now **ChatGPT Apps**. They are built with OpenAI's **Apps SDK**, which runs on the open **Model Context Protocol (MCP)**, and they are listed in the **ChatGPT App Directory**, which opened to third-party submissions in December 2025. ChatGPT has more than 800M weekly users, so a well-placed app gets in front of buyers at the moment they ask for help.

**The main constraint:** you can't yet sell digital goods, subscriptions or in-app services *inside* ChatGPT. Apps can only link out to complete purchases of physical goods. OpenAI says more monetization options are coming. That means **the money is made off-platform**: the app is a free acquisition channel that leads to a paid account, a booked call or a client retainer.

**Recommendation:** build three apps in sequence, all tied to marketing (our existing expertise and customer base), on one shared MCP codebase:

| # | App | Who pays | How we profit | Time to first $ |
|---|-----|----------|---------------|-----------------|
| 1 | **AdAudit** – paid-ads account auditor | SMBs and in-house marketers | Freemium audit → paid SaaS plan + agency leads | 6–8 weeks |
| 2 | **LocalRank** – local SEO and Google Business Profile optimizer | Local businesses and multi-location brands | SaaS subscription + done-for-you upsell | 8–10 weeks |
| 3 | **"ChatGPT App in a Box"** – white-label apps we build for clients | Brands and agencies | Build fee ($15–40k) + monthly retainer | Start selling now |

App #3 is a **service line** and is probably the fastest and highest-margin revenue. Apps #1 and #2 are our own products, and they double as live demos for #3.

---

## 2. Why marketing apps, and why us

- **High intent:** "Why is my CPA going up?" and "How do I rank in Google Maps?" are questions people already type into ChatGPT every day.
- **Clear ROI:** marketing spend is measurable, so a tool that saves 10% of ad spend is easy to sell.
- **Account linking is allowed:** apps can use OAuth to connect a user's Google Ads, Meta, GA4 or GBP account. That gives us real data, which makes the app useful and gives us a reason to bring the user back to our platform.
- **Weak competition in niche verticals:** large SaaS vendors such as HubSpot and Canva have apps, but specialized, opinionated tools (an auditor or optimizer, not a general CRM) are still thin.

---

## 3. App concepts in detail

### App 1 — AdAudit (flagship product)

**User prompt:** "Audit my Google Ads account" or "Why did my Meta ROAS drop last week?"

**What it does in ChatGPT:**
- Connects Google Ads, Meta Ads and GA4 through OAuth
- Shows an inline **audit scorecard widget** covering wasted spend, search-term leaks, bid strategy problems, creative fatigue and tracking gaps
- Lists the top 5 fixes ranked by estimated dollar impact
- Lets the user apply simple fixes (negative keywords, pausing ad sets) with confirmation

**Monetization (all off-platform):**
- Free: 1 audit per month, read-only
- **Pro, $49–99/month:** continuous monitoring, weekly alerts, one-click fixes, multiple accounts (billed on our site)
- **Agency, $299+/month:** client workspaces and white-label PDF reports
- **Lead capture:** accounts spending over $20k/month see a "Book a free strategy call with realitymarketing.ai" CTA

**Moat:** our own audit rules and benchmark data, which improve with every audit we run.

### App 2 — LocalRank

**User prompt:** "Why isn't my dental clinic showing in Google Maps?"

**What it does:**
- Reviews Google Business Profile completeness, categories, review velocity and NAP consistency
- Shows a map-grid rank widget
- Drafts review replies, GBP posts and Q&A content, then publishes them with approval

**Monetization:** $29–79 per location per month. Multi-location brands pay per-location enterprise pricing. Done-for-you management is a $500+/month upsell.

### App 3 — "ChatGPT App in a Box" (service line)

Many brands want to be in the App Directory but don't have MCP engineering skills. We sell:

| Package | Scope | Price |
|---------|-------|-------|
| **Starter** | Catalog or lead-gen app: product search, quote request, store locator | $15k build + $1.5k/month |
| **Growth** | Account-linked app with custom widgets and CRM integration | $30–40k + $3k/month |
| **Optimization retainer** | "App SEO": directory listing, prompt-discovery tuning, analytics | $2–5k/month |

Good-fit verticals include real estate (listing search), home services (instant quotes), e-commerce (product finder with outbound checkout, which is allowed for physical goods), travel and hospitality.

**Why it's the best ROI:** it needs no product-market-fit risk, brings in cash from the first client, and every build makes our shared framework stronger.

---

## 4. Ideas considered and deprioritized

| Idea | Why not now |
|------|-------------|
| Generic content writer or SEO blog generator | ChatGPT already does this natively, so there is no differentiation |
| Paid-per-use digital tools sold inside ChatGPT | Not allowed under current monetization rules |
| Consumer social-media caption apps | Low willingness to pay and crowded |
| Finance, health or legal advisory apps | Heavy policy review, liability, and teen-suitability requirements |

---

## 5. Technical approach

- **Stack:** TypeScript MCP server (Apps SDK) + React widgets rendered in ChatGPT + our existing auth/billing (Stripe) on realitymarketing.ai
- **Shared core:** one codebase that provides OAuth connectors (Google, Meta), a widget component library, usage metering and analytics, so each new app is mostly configuration plus domain logic
- **Portability:** MCP is an open standard, so the same servers can also run as Claude connectors and in other MCP-compatible assistants. That gives us more distribution for no extra build cost.
- **Compliance checklist for review:** privacy policy, support contact, least-privilege OAuth scopes, confirmations before any write action, no in-app ads, suitable for general audiences

---

## 6. Go-to-market

1. **Directory optimization:** write tool names and descriptions to match the natural-language prompts people actually use, so ChatGPT picks our app.
2. **Content loop:** publish anonymized "We audited 500 ad accounts" findings, which drives SEO and press and builds trust in the app.
3. **Existing clients:** onboard current agency clients first to collect reviews and case studies.
4. **Partner channel:** offer the Agency tier and white-label builds to smaller agencies.

---

## 7. Financial model (conservative, year 1)

| Stream | Assumption | Year-1 revenue |
|--------|-----------|----------------|
| App-in-a-Box builds | 6 clients × $25k avg | $150k |
| App-in-a-Box retainers | avg 4 active × $2.5k × 8 mo | $80k |
| AdAudit subscriptions | 250 paying by month 12 (ramp), avg $79 | ~$110k |
| LocalRank subscriptions | 300 locations by month 12 (ramp), avg $49 | ~$75k |
| Agency leads from apps | 5 new retainers × $3k × 6 mo | $90k |
| **Total** | | **≈ $500k** |

**Estimated costs:** 2 engineers + 0.5 designer + hosting/LLM ≈ $250–300k. **Payback is expected within year 1**, mostly from the service line.

---

## 8. Roadmap

| Phase | Weeks | Deliverables |
|-------|-------|--------------|
| 0 – Foundation | 1–3 | Shared MCP core, OAuth (Google/Meta), widget kit, Stripe linking |
| 1 – AdAudit MVP | 4–8 | Read-only audit + scorecard widget, directory submission |
| 1b – Sell service | 1–8 (parallel) | Sales deck + 2 pilot clients for App-in-a-Box |
| 2 – AdAudit Pro | 9–14 | Monitoring, alerts, write actions, paid tiers |
| 3 – LocalRank | 12–20 | GBP integration, rank grid, review responder |
| 4 – Scale | 20+ | Agency tier, partner program, port to Claude/other MCP hosts |

---

## 9. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| OpenAI changes directory or monetization rules | Monetize off-platform, use MCP to stay multi-platform, and adopt native payments if they ship |
| App review rejection or delays | Follow guidelines from day 1 and start with read-only scopes |
| Google/Meta API access limits | Apply for API developer access early and use existing agency MCC/BM relationships |
| OpenAI ships the feature natively | Focus on proprietary data and benchmarks plus done-for-you services that a platform won't offer |

---

## 10. Decisions needed

1. Approve the **App-in-a-Box service line** and start outreach to 10 target clients this month.
2. Approve **Phase 0 + AdAudit MVP** budget (~8 weeks, 2 engineers).
3. Choose the launch vertical for the first service-line pilot (recommended: home services or real estate).

---

### Sources
- [VentureBeat – OpenAI now accepting ChatGPT app submissions](https://venturebeat.com/technology/openai-now-accepting-chatgpt-app-submissions-from-third-party-devs-launches)
- [The Decoder – OpenAI launches app submissions](https://the-decoder.com/openai-launches-app-submissions-and-rolls-out-store-in-the-new-year/)
- [Moburst – ChatGPT App Directory explained](https://www.moburst.com/blog/chatgpt-app-directory-explained-what-brands-need-to-know/)

*Figures are planning estimates, not guarantees. Check OpenAI's current monetization policy before launch, because it is expected to change.*
