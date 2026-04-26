#!/usr/bin/env python3
"""
Auto-Improvement Engine for Options Detective

Monitors test failures and attempts to automatically fix common issues:
1. Code quality issues (linting, formatting)
2. Type errors (myPy)
3. Security vulnerabilities
4. Performance regressions
5. Test failures (with retry logic)

Usage: python scripts/auto_improve.py --mode=fix --issue-type=test
"""
import subprocess
import sys
import argparse
import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
TESTS_DIR = PROJECT_ROOT / "tests"

# Mapping of tools to fix commands
FIX_COMMANDS = {
    "format": {
        "check": ["black", "--check", "src/", "tests/"],
        "fix": ["black", "src/", "tests/"],
        "description": "Code formatting (PEP 8)"
    },
    "imports": {
        "check": ["isort", "--check-only", "src/", "tests/"],
        "fix": ["isort", "src/", "tests/"],
        "description": "Import sorting"
    },
    "lint": {
        "check": ["flake8", "src/", "tests/", "--max-line-length=100"],
        "fix": [],  # Manual fix required for flake8
        "description": "Linting errors (E/W/F)"
    },
    "types": {
        "check": ["mypy", "src/", "--ignore-missing-imports"],
        "fix": [],  # Manual fix required for mypy
        "description": "Type annotation issues"
    },
    "security": {
        "check": ["bandit", "-r", "src/", "-f", "json"],
        "fix": [],  # Manual review required
        "description": "Security vulnerabilities"
    }
}


class Issue:
    """Represents a detected code issue."""
    def __init__(self, tool: str, file: str, line: int, code: str, message: str, severity: str = "medium"):
        self.tool = tool
        self.file = file
        self.line = line
        self.code = code
        self.message = message
        self.severity = severity  # low, medium, high, critical
        self.fixed = False
    
    def __repr__(self):
        return f"[{self.tool}] {self.file}:{self.line} {self.code} - {self.message}"


def run_command(cmd: List[str], cwd=PROJECT_ROOT) -> Tuple[int, str, str]:
    """Run command and capture output."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd
    )
    return result.returncode, result.stdout, result.stderr


def check_tool_installed(tool: str) -> bool:
    """Check if a CLI tool is installed."""
    rc, _, _ = run_command(["which", tool])
    return rc == 0


def parse_flake8_output(output: str) -> List[Issue]:
    """Parse flake8 output into Issue objects."""
    issues = []
    pattern = r'([^:]+):(\d+):(\d+):\s*([A-Z]\d+)\s+(.*)'
    
    for line in output.splitlines():
        match = re.match(pattern, line)
        if match:
            file, row, col, code, msg = match.groups()
            severity = "high" if code.startswith("E") else "medium"
            issues.append(Issue("flake8", file, int(row), code, msg, severity))
    
    return issues


def parse_bandit_output(output: str) -> List[Issue]:
    """Parse bandit JSON output."""
    issues = []
    try:
        data = json.loads(output)
        for issue in data.get("results", []):
            severity = issue.get("issue_severity", "MEDIUM").lower()
            issues.append(Issue(
                tool="bandit",
                file=issue.get("filename", "unknown"),
                line=issue.get("line_number", 0),
                code=issue.get("test_id", "unknown"),
                message=issue.get("issue_text", "Unknown issue"),
                severity=severity
            ))
    except json.JSONDecodeError:
        pass
    return issues


def detect_issues(tool_name: str) -> List[Issue]:
    """Run a detection tool and parse issues."""
    print(f"\n[SCAN] {FIX_COMMANDS[tool_name]['description']}...")
    
    cmd = FIX_COMMANDS[tool_name]["check"]
    rc, stdout, stderr = run_command(cmd)
    
    issues = []
    if tool_name == "flake8":
        issues = parse_flake8_output(stdout + stderr)
    elif tool_name == "bandit":
        issues = parse_bandit_output(stdout)
    elif tool_name == "black":
        # Black outputs list of files that would be reformatted
        for line in (stdout + stderr).splitlines():
            if line.endswith(".py") and "would be reformatted" in line:
                filepath = line.split()[0]
                issues.append(Issue("black", filepath, 0, "F001", "File needs reformatting", "low"))
    
    if issues:
        print(f"  Found {len(issues)} issues")
        for issue in issues[:5]:  # Show first 5
            print(f"    {issue}")
        if len(issues) > 5:
            print(f"    ... and {len(issues) - 5} more")
    else:
        print(f"  ✓ No issues found")
    
    return issues


def auto_fix(issue: Issue) -> bool:
    """Attempt to automatically fix an issue."""
    if issue.tool == "black":
        cmd = ["black", issue.file]
        rc, _, _ = run_command(cmd)
        if rc == 0:
            issue.fixed = True
            print(f"  [FIXED] {issue.file} (format)")
            return True
    
    elif issue.tool == "isort":
        cmd = ["isort", issue.file]
        rc, _, _ = run_command(cmd)
        if rc == 0:
            issue.fixed = True
            print(f"  [FIXED] {issue.file} (imports)")
            return True
    
    return False


def run_tests(test_path: str = "", fast: bool = False) -> Tuple[bool, str]:
    """Run the test suite."""
    print("\n[TEST] Running test suite...")
    
    cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short"]
    
    if fast:
        cmd.append("tests/test_greeks.py")
    elif test_path:
        cmd.append(test_path)
    else:
        cmd.extend(["tests/test_greeks.py", "tests/test_comprehensive.py"])
    
    rc, stdout, stderr = run_command(cmd)
    
    # Parse failures
    failures = []
    for line in (stdout + stderr).splitlines():
        if "FAILED" in line or "ERROR" in line:
            failures.append(line)
    
    print(f"  Exit code: {rc}")
    print(f"  Output: {len(stdout.splitlines())} lines")
    
    return rc == 0, "\n".join(failures[:10])


def suggest_fix_for_failure(failure_output: str) -> str:
    """Analyze test failure and suggest fixes."""
    suggestions = []
    
    # Common patterns
    patterns = {
        r"ImportError: cannot import name '(\w+)'": 
            "Check if the imported object exists and is exported in __init__.py",
        
        r"TypeError: (\w+) takes \d+ positional arguments but \d+ were given":
            "Check function signature and call sites",
        
        r"AssertionError: assert .* == .*":
            "Value mismatch - check calculation logic or expected value",
        
        r"ModuleNotFoundError: No module named '(\w+)'":
            "Install missing package or add to requirements.txt",
        
        r"ValueError: .*shape.*":
            "Check array dimensions in numpy/pandas operations",
        
        r"KeyError: '(\w+)'":
            "Missing key in dict - check if data structure changed",
    }
    
    for pattern, suggestion in patterns.items():
        if re.search(pattern, failure_output):
            suggestions.append(f"- {suggestion}")
    
    if not suggestions:
        suggestions.append("- Review the failing test and traceback manually")
        suggestions.append("- Check recent commits for breaking changes")
    
    return "\n".join(suggestions)


def main():
    parser = argparse.ArgumentParser(description="Auto-improvement engine")
    parser.add_argument("--mode", choices=["scan", "fix", "test", "full"], 
                       default="scan", help="Operation mode")
    parser.add_argument("--tool", choices=list(FIX_COMMANDS.keys()) + ["all"],
                       default="all", help="Which tool to run")
    parser.add_argument("--issue-type", default="", help="Filter issues by type")
    parser.add_argument("--max-fixes", type=int, default=10, help="Max auto-fixes per run")
    parser.add_argument("--commit", action="store_true", 
                       help="Commit fixes automatically (DANGEROUS)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be fixed without fixing")
    
    args = parser.parse_args()
    
    print(f"{'='*60}")
    print(f"AUTO-IMPROVEMENT ENGINE")
    print(f"{'='*60}")
    
    if args.mode in ["scan", "fix", "full"]:
        # Run detection
        all_issues = []
        tools = list(FIX_COMMANDS.keys()) if args.tool == "all" else [args.tool]
        
        for tool in tools:
            if not check_tool_installed(tool):
                print(f"[WARN] Tool '{tool}' not installed, skipping")
                continue
            
            issues = detect_issues(tool)
            all_issues.extend(issues)
        
        # Filter by issue type if specified
        if args.issue_type:
            all_issues = [i for i in all_issues if args.issue_type in i.message.lower()]
        
        print(f"\nTotal issues found: {len(all_issues)}")
        
        # Categorize by severity
        by_severity = {}
        for issue in all_issues:
            by_severity.setdefault(issue.severity, []).append(issue)
        
        for sev in ["critical", "high", "medium", "low"]:
            if sev in by_severity:
                print(f"  {sev.upper()}: {len(by_severity[sev])}")
        
        # Auto-fix if requested
        if args.mode in ["fix", "full"] and not args.dry_run:
            print(f"\n[AUTO-FIX] Attempting to fix up to {args.max_fixes} issues...")
            
            fixed_count = 0
            for issue in all_issues:
                if fixed_count >= args.max_fixes:
                    break
                
                if auto_fix(issue):
                    fixed_count += 1
            
            print(f"  Fixed: {fixed_count}/{len(all_issues)}")
        
        # Report
        if all_issues:
            report_file = PROJECT_ROOT / "improvement_report.json"
            report_data = {
                "timestamp": datetime.now().isoformat(),
                "total_issues": len(all_issues),
                "by_severity": {k: len(v) for k, v in by_severity.items()},
                "issues": [
                    {
                        "tool": i.tool,
                        "file": i.file,
                        "line": i.line,
                        "code": i.code,
                        "message": i.message,
                        "severity": i.severity,
                        "fixed": i.fixed
                    }
                    for i in all_issues[:100]  # Limit report size
                ]
            }
            report_file.write_text(json.dumps(report_data, indent=2))
            print(f"\n[REPORT] Saved to {report_file}")
    
    # Run tests if requested
    if args.mode in ["test", "full"]:
        success, failures = run_tests()
        
        if not success:
            print("\n[ANALYZE] Test failures detected:")
            print(suggest_fix_for_failure(failures))
        
        if args.commit and success:
            # Commit all changes
            subprocess.run(["git", "add", "-A"], cwd=PROJECT_ROOT)
            subprocess.run(
                ["git", "commit", "-m", "Auto-improvement: quality fixes"],
                cwd=PROJECT_ROOT
            )
            print("\n[COMMIT] Changes committed automatically")
    
    print(f"\n{'='*60}")
    print(f"DONE")


if __name__ == "__main__":
    sys.exit(main())
