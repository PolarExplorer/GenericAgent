# GA SDD Overlay

This is a lightweight GA-specific overlay inspired by GitHub Spec Kit.

Upstream mirror: `../spec-kit-upstream`  
Current upstream HEAD captured separately by git.

## Design

- Do not modify upstream directly.
- Keep GA logic in this overlay.
- Use Spec Kit templates as reference, not as a second GA SOP system.
- Bridge GA SOPs: project_context_sop, plan_sop, verify_sop, contract_sop, product_dev_sop.

## Minimal commands

```bash
python ga_sdd.py init <project_root>
python ga_sdd.py check <project_root>
python ga_sdd.py sync <project_root>
```

## Document roles

- `spec.md`: what/why/boundary/acceptance
- `plan.md`: technical path/gates/verification
- `tasks.md`: executable task ledger
- `AGENTS.md`: AI operation map, not encyclopedia
