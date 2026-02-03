# Crowd IT Skills Library

Centralized AI skills for MSP operations, radiology workflows, and automation. These skills are automatically synced to GCS on every deployment and accessible by:

- **Claude** via `https://storage.googleapis.com/crowdit-skills/`
- **OpenClaw** via local filesystem sync
- **n8n** via HTTP requests

## Structure

```
skills/
├── index.json              # Skill catalog with metadata
├── msp/                    # MSP operations skills
│   ├── ticket-triage.md
│   └── billing-reconciliation.md
├── radiology/              # Medical imaging skills
│   └── study-volume-monitoring.md
└── templates/              # Skill creation templates
    └── skill-template.md
```

## Adding a New Skill

1. Copy `templates/skill-template.md` to the appropriate category folder
2. Fill in the skill details following the template format
3. Update `index.json` with the new skill metadata
4. Commit and push - Cloud Build will sync to GCS automatically

## Skill Format

Each skill uses YAML frontmatter for metadata:

```yaml
---
name: skill-name
category: msp
version: 1.0.0
author: chris@crowdit.com.au
tags: [tag1, tag2]
platforms: [claude, openclaw, n8n]
requires_tools: [tool1, tool2]
---
```

## Accessing Skills

### Direct URL
```
https://storage.googleapis.com/crowdit-skills/msp/ticket-triage.md
```

### Index (all skills)
```
https://storage.googleapis.com/crowdit-skills/index.json
```

## Available Skills

| Skill | Category | Description |
|-------|----------|-------------|
| ticket-triage | msp | Prioritize support tickets by impact and SLA |
| billing-reconciliation | msp | Compare Pax8 subscriptions to Xero invoices |
| study-volume-monitoring | radiology | Monitor imaging volumes via BigQuery |
