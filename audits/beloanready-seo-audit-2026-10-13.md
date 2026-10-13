# SEO audit: beloanready.com

**Date:** 2026-10-13
**Scope:** beloanready.com (Floot project "Loan Ready — Small Business Loan Readiness Calculator", also served at getloanready.floot.app)
**Method:** Source-code review of the published Floot project (last publish 2026-10-11). The live site could not be crawled from the audit environment (outbound network policy), so HTTP-level checks are listed under "Not verified" at the end.

---

## Summary

The technical base is in good shape. robots.txt and the sitemap are in place, canonicals point every hostname at beloanready.com, app screens are `noindex`, SSR is on, and `lang="en"` is set. The problems are visibility and content. Google sees a ~200-word homepage with no call to action, no links into the rest of the site, no social or structured data, and a page that says it is "still under construction". For a finance product (Google treats this as YMYL, "Your Money or Your Life"), that is the main thing holding it back.

| Area | Status |
|---|---|
| Crawlability (robots, sitemap, canonical, noindex) | Good, with two gaps |
| On-page (titles, descriptions, headings) | Mixed. Inner pages are fine; the homepage relies on a fallback |
| Internal linking | **Poor.** The homepage links nowhere indexable |
| Content depth | **Thin.** About 200 words on the homepage; glossary entries average about 80 words |
| Structured data / social previews | **Missing** |
| Trust signals (E-E-A-T) | **Weak** for a YMYL finance topic |

---

## Priority 1: fix this week

### 1. Server-rendered homepage hides every CTA and the price
`pages/_index.tsx` renders `<LandingPage showActions={authState.type === "unauthenticated"} />`. During SSR the session query is still `pending`, so `authState` is `"loading"` and `showActions` is false. The HTML that Googlebot first receives therefore has:
- no "Sign in or create an account" or "Get started" links (no link to `/login` at all),
- no price note ($10.99 for three calculations, refund terms),
- no closing "Walk in prepared." section.

**Fix:** treat `loading` like `unauthenticated` for the marketing content. Render the CTAs and price whenever the user is not authenticated (`showActions={authState.type !== "authenticated"}`). Only the swap to the calculator should wait for auth.

### 2. The homepage has no internal links to indexable pages
`LandingPage.tsx` links only to `/` and `/login` (which is `noindex`), plus a `mailto:`. `/definitions`, its 26 term pages and `/privacy` are linked only from inside the signed-in calculator (InfoBubbles) or from Terms modals. To Google they are orphans that only the sitemap points to.

**Fix:** add a real site footer to `LandingPage` (and ideally the definitions and privacy pages) with links to *Loan terms in plain language* (`/definitions`), *Privacy Policy* (`/privacy`), and, if you want it found, *For partners* (`/partners`). Add a "Learn the terms lenders use →" link in the "How it works" section.

### 3. No Open Graph, Twitter or structured data anywhere
Links shared on LinkedIn, Facebook, Slack, iMessage and similar sites will show no image and a guessed title.

**Fix:** add default tags in `SeoTags` in `components/_globalContextProviders.tsx`, and let pages override them:
```tsx
<meta property="og:site_name" content="Loan Ready" />
<meta property="og:type" content="website" />
<meta property="og:url" content={canonicalUrl} />
<meta property="og:image" content="https://beloanready.com/_cdn/static/<1200x630-share-image>.png" />
<meta name="twitter:card" content="summary_large_image" />
```
Then add JSON-LD:
- **Homepage:** `Organization` (name "5 Cs Business Solutions Inc.", brand "Loan Ready", logo, `contactPoint` email, `address` New Brunswick, CA) plus `WebApplication` / `SoftwareApplication` (`applicationCategory: "FinanceApplication"`, `offers` price 10.99 CAD).
- **`/definitions/:term`:** `DefinedTerm` inside a `DefinedTermSet`, plus `BreadcrumbList` (Home › Definitions › Term).
- **Homepage FAQ** (once written, see P2): `FAQPage`.

### 4. Give the homepage its own explicit title and description
`LandingPage` sets no `<Helmet><title>`, so the homepage uses the project-metadata fallback, "Loan Ready — Small Business Loan Readiness Calculator". That title is good, but the 173-character description will be cut off (about 155–160 is the limit), and the fallback is fragile if the metadata ever changes. Set both explicitly in `LandingPage`, for example:
- **Title:** `Small Business Loan Readiness Calculator | Loan Ready` (53 chars)
- **Description:** `See how a bank is likely to view your small business loan application before you apply. A plain-language score on cash flow, credit, equity and experience.` (≈155 chars)

### 5. Remove "still under construction" from the public footer
For a YMYL finance tool, "Loan Ready is still under construction" works against trust with users and with Google's quality guidelines. Replace it with a neutral feedback line, such as "Tell us what would make Loan Ready more useful: support@…".

---

## Priority 2: next 2–4 weeks

### 6. Homepage content is thin and misses the main keyword
- The H1 is "Know where you stand before the bank does." It works as a tagline, but it contains no search term. Keep it, and put the keyword in the eyebrow or subtitle, or add an H2: "A loan readiness check for small business owners".
- There are about 200 words of indexable copy. Add 500–800 words of useful content:
  - **What lenders look at.** A short, non-proprietary walk-through of the factors you score: cash flow / debt service, credit, equity / net worth, experience, financial statements. Link each to its `/definitions/` page. The company name, *5 Cs*, maps directly onto the well-known "5 Cs of credit" (character, capacity, capital, collateral, conditions). That is a natural, high-intent topic to own.
  - **Who it's for.** Startups, first-time borrowers, owners preparing for a bank, credit union or BDC meeting.
  - **FAQ** (4–6 questions, marked up as `FAQPage`): "Will this affect my credit score?", "Is my data stored?" (no, the raw figures are not kept, which is a strong selling point), "How much does it cost?", "Does a good score mean I'll be approved?", "What documents do I need?".
- Say "Canada" explicitly somewhere. The business, currency (CAD), privacy regulator and data residency are all Canadian, but the copy never says so. If Canada is the main market, aim for Canadian terms ("small business loan Canada", "BDC", "credit union") and mark the price "$10.99 CAD" on the landing page to match the Terms.

### 7. Turn the glossary into real landing pages
The 26 `/definitions/:term` pages are a good base for long-tail search ("what is business tangible net worth", "personal net worth for a business loan"), but at the moment:
- each one is about 50–120 words, which is thin;
- there is no route back to the product. The footer says "You can close this tab to get back to your calculator", which only makes sense for someone who came from the app;
- they do not link to each other.

**Fix:** expand the high-value terms first (`existingDebtPayments` / debt service, `equity`, `creditScore`, `personalNetWorth`, `businessTangibleNetWorth`, `statements`) to 300–500 words each with an example. Add a "Check your own loan readiness →" CTA to `/`, a "Related terms" block, and breadcrumbs. Adjust the footer note so it only shows when the page was opened from the calculator.

### 8. Decide what `/partners` should be
On 2026-10-06 `/partners` was removed from public links and from the sitemap on purpose, but the page is still indexable (no `noindex`, and it has a canonical). Pick one:
- **Hidden for now:** add `/partners` to `NOINDEX_PATHS`.
- **Ready to sell:** put it back in the sitemap and footer. Its title and description are already well written.

### 9. Soft-404 on unknown definition terms
`/definitions/<anything>` returns a 200 "We couldn't find that definition" page with a canonical and no `noindex`. Add `<meta name="robots" content="noindex" />` in the not-found branch of `pages/definitions.$term.tsx`. Also check what Floot returns for unknown top-level routes.

### 10. Add E-E-A-T and trust signals
- Add an **About** page covering who is behind 5 Cs Business Solutions Inc., their lending or advisory background, and why the tool exists. If there is a credentialed person (banker, CPA), name them.
- Name the legal entity and location in the homepage footer (currently they appear only in the Privacy Policy and Terms).
- Show a short **Methodology** summary of what is scored and what is not, without disclosing the proprietary thresholds.
- Once you have them, show testimonials and partner logos (accountants or credit unions).

---

## Priority 3: housekeeping

11. **Sitemap.** Add `<lastmod>` dates (Google ignores `changefreq` and `priority`). Add `/partners` if you choose to index it (#8).
12. **Glossary URL slugs** are camelCase (`/definitions/monthlyRevenue`). Lowercase-hyphenated slugs (`/definitions/monthly-revenue`) are the norm and read better in results. The site is new, so this is the cheapest moment to change them, but **only if Floot can issue 301 redirects** from the old URLs. If it can't, leave them.
13. **Duplicate site-verification tag.** `google-site-verification` is emitted both globally and again in `pages/_index.tsx` for signed-in users. Remove the one in `_index.tsx`.
14. **`noindex, nofollow` on `/login`.** `noindex` is right. `nofollow` is unnecessary and harmless.
15. **Partner-referral URLs** (`/?p=<slug>`) already canonicalize to `/`. No change needed. Keep it that way.
16. **Share image.** Create a 1200×630 OG image in the brand style (score ring on ivory) to support #3.

---

## What's already right

- `robots.txt` allows everything and points at `https://beloanready.com/sitemap.xml`.
- A single canonical origin (`https://beloanready.com`) covers both custom domains and the `floot.app` subdomain, and query strings are stripped.
- `/login`, `/partner/login` and `/partner/dashboard` are `noindex`.
- SSR is enabled, so pages are not an empty JS shell. Per-page `<title>` and description are set on `/definitions`, `/definitions/:term`, `/privacy` and `/partners`.
- Heading structure is clean: one H1 per page, logical H2/H3.
- Accessibility work (skip link, landmarks, `lang`, contrast) also helps SEO.
- Google Search Console verification tag is in place.

---

## Not verified (needs a live crawl)

The audit environment could not reach beloanready.com. Check these in Search Console, PageSpeed Insights, or a crawler such as Screaming Frog:
- `www.beloanready.com` → `beloanready.com` 301 (both are attached in Floot; confirm one redirects to the other rather than both serving 200).
- HTTP → HTTPS redirect and certificate validity.
- That SSR output actually contains the Helmet `<title>`, description, and canonical (view source, not DevTools).
- Core Web Vitals (LCP, INP, CLS), especially the hero animation and web-font loading (Fraunces / Inter Tight).
- Index coverage in Search Console: how many of the 29 sitemap URLs are indexed.
- Backlinks and branded search. A web search for "beloanready" returned no results for the domain, which is expected for a site launched in October 2026.

---

## Suggested order of work

1. P1 #1, #2, #4, #5. These are small code changes in `LandingPage.tsx`, followed by a republish.
2. P1 #3: OG tags, share image, Organization/WebApplication JSON-LD.
3. P2 #8, #9: one-line `noindex` changes.
4. P2 #6, #7, #10: content work (homepage sections and FAQ, expanded glossary, About page).
5. Submit the sitemap again in Search Console and request indexing for `/` and `/definitions`.
