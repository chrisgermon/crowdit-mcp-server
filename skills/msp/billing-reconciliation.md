---
name: billing-reconciliation
category: msp
version: 1.0.0
author: chris@crowdit.com.au
created: 2026-02-03
updated: 2026-02-03
tags: [billing, xero, pax8, halopsa, reconciliation, invoicing]
platforms: [claude, openclaw, n8n]
requires_tools: [pax8_list_subscriptions, pax8_list_companies, xero_get_invoices, xero_get_contacts, halopsa_get_recurring_invoices, halopsa_get_clients]
---

# Billing Reconciliation

## Purpose

Compare subscriptions across billing platforms (Pax8, HaloPSA recurring invoices) against Xero invoices to identify revenue leakage, unbilled services, and discrepancies.

## When to Use

Trigger this skill when:
- User asks about billing discrepancies
- Weekly/monthly billing review
- Client asks "am I being billed correctly?"
- Checking for unbilled subscriptions
- Verifying Pax8 costs vs customer billing
- Keywords: reconcile, billing, subscription, invoice mismatch

Do NOT use this skill when:
- Creating a new invoice (use invoice-creation skill)
- Looking up a specific invoice
- General accounting questions

## Instructions

### Step 1: Gather Subscription Data

```python
# Get all active Pax8 subscriptions
pax8_list_subscriptions(status="Active", size=200)

# Get Pax8 companies for mapping
pax8_list_companies()
```

### Step 2: Gather Billing Data

```python
# Get HaloPSA recurring invoices
halopsa_get_recurring_invoices(active_only=True)

# Get recent Xero invoices
xero_get_invoices(days=90, status="AUTHORISED")

# Get Xero contacts for mapping
xero_get_contacts(is_customer=True)
```

### Step 3: Compare and Report

For each Pax8 subscription:
1. Find matching Xero invoice line items
2. Compare quantities and calculate margin
3. Identify discrepancies: unbilled, quantity mismatch, price mismatch

### Output Format

```markdown
## Billing Reconciliation Report
**Period:** [Month Year]
**Generated:** [Date Time]

### Summary
| Metric | Value |
|--------|-------|
| Total Pax8 Subscriptions | XX |
| Matched | XX |
| Discrepancies | XX |
| **Potential Monthly Leakage** | $X,XXX |

### 🔴 Critical Discrepancies (>$100/mo)
[Details]

### Recommendations
1. [Action item]
```

## Tool Reference

| Tool | Purpose |
|------|---------|
| `pax8_list_subscriptions` | Get Pax8 subscriptions |
| `pax8_list_companies` | Get Pax8 company list |
| `xero_get_invoices` | Get Xero invoices |
| `halopsa_get_recurring_invoices` | Get recurring invoices |

## Notes

- Target margin for Microsoft products: 25-35%
- Target margin for Azure: 20-30%
- Run reconciliation weekly minimum
