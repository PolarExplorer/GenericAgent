# tasks.md — [Project/Feature]

**Input**: spec.md, plan.md, AGENTS.md  
**Rule**: each task must be executable without extra context and include file paths when possible.

## Format
- `[P]` means parallelizable
- `[USx]` links the task to a user story
- Use exact paths whenever possible

## Phase 1: Setup / Foundation
- [ ] T001 Create/update project structure per plan.md
- [ ] T002 Identify affected files and verification commands

## Phase 2: User Story 1 — [Title] (P1 / MVP)
**Goal**: ...  
**Independent Test**: ...

### Tests / Checks
- [ ] T003 [P] [US1] Add/prepare verification for ... in `path/to/test_or_check`

### Implementation
- [ ] T004 [US1] Implement ... in `path/to/file`
- [ ] T005 [US1] Update docs/context if needed in `AGENTS.md` or `spec.md`

## Phase 3: Polish / Cross-cutting
- [ ] T006 Run verification commands from plan.md
- [ ] T007 Run SDD sync/check and fix documentation drift

## Dependency Notes
- T001 before implementation tasks
- Tests/checks before or alongside implementation when practical
