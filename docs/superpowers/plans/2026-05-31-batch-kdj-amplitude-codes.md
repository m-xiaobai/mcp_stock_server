# Batch KDJ And Amplitude Codes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update `compute_kdj` and `compute_amplitude` so they accept `codes: list[str]` and return per-code results in one response.

**Architecture:** Reuse a single `StockDailyService.get_stock_daily_bars(...)` call for all requested codes, then compute each indicator per returned item and serialize them into an `items` list. Keep failure behavior strict by raising when any requested code has no daily bars.

**Tech Stack:** Python, unittest, FastMCP

---

### Task 1: Add Failing Batch Tests

**Files:**
- Modify: `tests/test_mcp_stock_server.py`
- Test: `tests/test_mcp_stock_server.py`

- [ ] **Step 1: Write the failing test**

Add tests for:
- `compute_kdj_by_code_tool(..., codes=[...])` returning `{"time": ..., "items": [...]}` with two code entries
- `compute_amplitude_by_code_tool(..., codes=[...])` returning `{"time": ..., "items": [...]}` with two code entries
- registered MCP tools `compute_kdj` and `compute_amplitude` accepting `codes`

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s tests -p test_mcp_stock_server.py`
Expected: FAIL due to old single-`code` signatures / payloads

- [ ] **Step 3: Write minimal implementation**

No implementation in this task.

- [ ] **Step 4: Run test to verify it still fails**

Run: `python -m unittest discover -s tests -p test_mcp_stock_server.py`
Expected: same failures

### Task 2: Implement Batch Indicator Loading

**Files:**
- Modify: `tools/indicator_tools.py`

- [ ] **Step 1: Write the failing test**

Covered by Task 1.

- [ ] **Step 2: Run test to verify it fails**

Covered by Task 1.

- [ ] **Step 3: Write minimal implementation**

Add a helper that loads bars for multiple codes in one query, validates every requested code has bars, and returns a mapping keyed by `code`. Update:
- `compute_kdj_by_code_tool(..., codes: list[str], ...)`
- `compute_amplitude_by_code_tool(..., codes: list[str], ...)`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s tests -p test_mcp_stock_server.py`
Expected: batch indicator tests pass

### Task 3: Update MCP Tool Signatures

**Files:**
- Modify: `server.py`

- [ ] **Step 1: Write the failing test**

Covered by Task 1 MCP registration assertions.

- [ ] **Step 2: Run test to verify it fails**

Covered by Task 1.

- [ ] **Step 3: Write minimal implementation**

Update `compute_kdj` and `compute_amplitude` tool signatures from `code: str` to `codes: list[str]` and pass them through to the tool layer.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s tests -p test_mcp_stock_server.py`
Expected: targeted registration tests pass

### Task 4: Final Verification

**Files:**
- Modify: `tools/indicator_tools.py`
- Modify: `server.py`
- Modify: `tests/test_mcp_stock_server.py`

- [ ] **Step 1: Run focused verification**

Run: `python -m unittest discover -s tests -p test_mcp_stock_server.py`
Expected: updated test file passes in the correctly configured environment

- [ ] **Step 2: Review diff for scope**

Run: `git diff -- tools/indicator_tools.py server.py tests/test_mcp_stock_server.py`
Expected: only batch-code related changes
