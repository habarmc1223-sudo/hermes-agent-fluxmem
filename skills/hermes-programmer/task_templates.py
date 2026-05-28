"""
Structured task templates for Hermes Programmer Agent.

Available templates: build_feature, debug_issue, refactor, code_review, write_tests.
Each template has: system_prompt, user_prompt_template, expected_output_format.
"""

TEMPLATES = {
    "build_feature": {
        "role": "coding",
        "description": "Build a new feature from specification",
        "system_prompt": (
            "You are a senior software engineer building production features. "
            "Follow existing project patterns. Write clean, tested code. "
            "Prefer editing existing files over creating new ones. "
            "No comments unless the WHY is non-obvious."
        ),
        "user_prompt": """
## Feature Specification
{description}

## Files to Modify
{files}

## Constraints
- Follow existing project patterns and conventions
- Reuse existing utilities where possible
- Write tests matching existing test style
- Return a complete diff or file listing

## Context
{context}

## Output Format
1. Plan: what files change and why (2-3 sentences)
2. Changes: complete file content or unified diff
3. Tests: new/modified test code
""",
    },
    "debug_issue": {
        "role": "debug",
        "description": "Debug and fix a reported issue",
        "system_prompt": (
            "You are a debugging expert. Find the root cause, not symptoms. "
            "Propose the MINIMAL fix. Explain your reasoning step by step. "
            "Consider edge cases and regression risk."
        ),
        "user_prompt": """
## Issue Description
{description}

## Relevant Files
{files}

## Reproduction Steps
{context}

## Output Format
1. Root cause: what's actually broken (1-2 sentences)
2. Why it happens: trace the bug path
3. Fix: minimal code change (diff)
4. Verification: how to confirm the fix works
5. Regression risk: what else might break (low/med/high)
""",
    },
    "refactor": {
        "role": "coding",
        "description": "Refactor code for clarity, performance, or maintainability",
        "system_prompt": (
            "You are a refactoring specialist. Improve code WITHOUT changing behavior. "
            "Follow the project's existing patterns. "
            "Focus on: readability, DRY principle, consistent naming, performance."
        ),
        "user_prompt": """
## Refactoring Goal
{description}

## Target Files
{files}

## Constraints
- Do NOT change external behavior or public APIs
- Follow existing patterns
- All existing tests must still pass
- Prefer small, reviewable changes

## Context
{context}

## Output Format
1. What changes and why (2-3 sentences)
2. Before/after: show the structural change
3. Complete refactored code or diff
""",
    },
    "code_review": {
        "role": "review",
        "description": "Review code for bugs, security, quality issues",
        "system_prompt": (
            "You are a thorough code reviewer. Find real issues, not style nits. "
            "Prioritize: security > correctness > performance > readability. "
            "Report only high-confidence findings. Skip obvious patterns used throughout the project."
        ),
        "user_prompt": """
## Review Target
{description}

## Files to Review
{files}

## Context
{context}

## Output Format
For each finding:
- [PRIORITY] File:Line — Issue description
- Why it matters
- Suggested fix

Priority levels: 🔴 critical, 🟡 medium, 🟢 low
""",
    },
    "write_tests": {
        "role": "test_writer",
        "description": "Write comprehensive tests for given code",
        "system_prompt": (
            "You are a test engineer. Write thorough, readable tests. "
            "Cover: happy path, edge cases, error conditions, boundary values. "
            "Match the existing test framework and style exactly."
        ),
        "user_prompt": """
## Code Under Test
{description}

## Files to Test
{files}

## Existing Test Patterns
{context}

## Output Format
1. Test plan: what's covered (happy path, edge cases, errors)
2. Test code: complete, runnable test file
3. Run command: how to execute these tests
""",
    },
}


def render_template(template_name: str, description: str = "", files: list = None, context: str = "") -> tuple:
    """Render a task template into (system_prompt, user_prompt)."""
    tpl = TEMPLATES.get(template_name)
    if not tpl:
        raise ValueError(f"Unknown template: {template_name}. Available: {list(TEMPLATES)}")

    files_text = "\n".join(f"- {f}" for f in (files or [])) or "No specific files"
    user_prompt = tpl["user_prompt"].format(
        description=description or "Execute the task",
        files=files_text,
        context=context or "Standard execution",
    )
    return tpl["system_prompt"].strip(), user_prompt.strip(), tpl["role"]
