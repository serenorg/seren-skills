# Seren Bucks V1 Outreach Email Templates

Canonical copy for outreach drafts. Claude MUST pull from these templates
when drafting new-outbound messages for the default SerenBucks program,
rather than synthesizing commission language from scratch.

## Program structure to disclose (non-negotiable)

SerenBucks is a 3-tier unilevel affiliate program. Every outreach email
MUST reflect the following flow without shortcuts:

1. The recipient is being invited to **join** as an affiliate.
2. The link in the email is the **sender's** `SRN_` recruitment link. It
   attaches the recipient as a Tier 1 downstream **only after the
   recipient signs up via the link**.
3. Once signed up, the recipient receives **their own unique `SRN_`
   code** and is the Tier 0 affiliate for anything *they* refer.
4. Commission rates:
   - **Tier 0 (recipient's own referrals):** 20% direct commission
   - **Tier 1 override (paid to their sponsor, i.e. the sender):** 5%
   - **Tier 2 override (paid to sponsor's sponsor):** 5%

Never tell the recipient they earn 20% just by forwarding the sender's
link. That credits the sender, not them.

## Template: Recruitment outreach (default)

Placeholders:

- `{{recipient_first_name}}` — e.g. `Erik`
- `{{sender_first_name}}` — e.g. `Taariq`
- `{{sender_full_name}}` — e.g. `Taariq Lewis`
- `{{sender_link}}` — sender's bootstrapped tracked link, e.g. `https://serendb.com?ref=SRN_TUQ4PQE2`
- `{{personal_hook}}` — one-sentence personalized opener grounded in CRM signal

Subject options:

- `Join SerenBucks and earn 20% on your own referrals`
- `SerenBucks affiliate invite — your own referral code inside`

Body:

```
Hi {{recipient_first_name}},

{{personal_hook}}

We're opening up the SerenBucks Affiliate Program and I'd like to sponsor
you in. Plus, every week SerenDB runs a $250 "Largest Purchase" contest —
make a purchase and you could win.

Here's how it works:

1. Join via my sponsor link below. You'll get your own unique SerenBucks
   referral code (an SRN_ link of your very own).
2. Share YOUR code with your network. You earn 20% commission on every
   SerenDB signup and usage that comes through YOUR link.
3. As your sponsor, I earn a 5% network override from your activity —
   that's why I'm happy to bring you in. No cost to you.

Join here (this enrolls you under me as sponsor):
{{sender_link}}

After signup you'll get your own code — that's the one you send to
friends.

Cheers,
{{sender_full_name}}
```

## What the draft MUST NOT say

- "You earn 20% on this link" (the link is the sender's, not theirs).
- "Just share this link to earn commissions" (same reason).
- "No signup required" (signup is how they get their own `SRN_` code).

## Validation checklist before approval

- Body contains the sender's exact bootstrapped `tracked_link`.
- Body contains the three-step flow (join → get own code → share own code).
- Body discloses both the recipient's 20% and the sender's 5% override.
- Subject and body do not claim the recipient earns from forwarding the
  sender's link.

## Template: Contest winner notification (new)

Subject: You won $250 in the SerenBucks Weekly Contest!

Body:

```
Hi {{winner_first_name}},

Congratulations! Your purchase of {{purchase_amount}} this week made you one of the top 2 buyers in the SerenBucks Weekly Contest.

You've won $250 in SerenBucks, which will be released after the 90-day hold period.

Keep using SerenDB and sharing your referral code to maximize your earnings!

Cheers,
The SerenDB Team
```
