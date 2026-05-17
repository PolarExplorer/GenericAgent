# Spec Kit -> GA Mapping

| Spec Kit | GA equivalent | Keep? | Notes |
|---|---|---:|---|
| constitution | project principles / AGENTS.md rules | partial | avoid duplicating GA constitution |
| specify | spec.md workflow | yes | what/why/boundary |
| clarify | clarification gate | yes | max critical questions, avoid analysis paralysis |
| plan | plan_sop | route | GA plan_sop remains source |
| tasks | tasks.md | yes | user-story phases, executable tasks |
| analyze | consistency check | yes | spec/plan/tasks/AGENTS coverage |
| implement | GA execution + verify_sop | route | do not replace GA execution discipline |
| integrations | GA model/subagent dispatch | no in MVP | heavy upstream ecosystem |
| extensions/presets | future GA plugins | later | not MVP |

## Sync rules

- spec changes -> check plan.md, tasks.md, AGENTS.md, contracts/, validation.md
- plan changes -> check tasks.md and AGENTS.md if commands/architecture changed
- tasks changes -> check plan coverage
- implementation done -> run check + verify, then reverse-sync docs if needed
