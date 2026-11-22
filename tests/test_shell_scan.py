"""Direct unit tests for hooks/scripts/shell_scan.py, the Bash guard's shell scanner.
Run: python3 -m unittest tests.test_shell_scan"""
import ast
import os
import sys
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks", "scripts")
sys.path.insert(0, SCRIPTS)
import shell_scan  # noqa: E402


def scan(text):
    """(full, main, heredocs, data spans, longest quote)."""
    return shell_scan.scan_shell(text)


class WordsTests(unittest.TestCase):
    def test_words_and_operators(self):
        self.assertEqual(shell_scan.shell_words("a&&b; c|d >f <<<h 2>&1"),
                         ["a", "&&", "b", ";", "c", "|", "d", ">", "f", "<<<", "h", "2", ">&", "1"])

    def test_newline_is_an_operator(self):
        self.assertEqual(shell_scan.shell_words("a\nb"), ["a", "\n", "b"])

    def test_quotes_are_removed_from_words(self):
        self.assertEqual(shell_scan.shell_words("echo 'a b' \"c d\" e\\ f"),
                         ["echo", "a b", "c d", "e f"])

    def test_unbalanced_quote_falls_back_to_a_rough_split(self):
        self.assertEqual(shell_scan.shell_words("rm x 'a"), ["rm", "x", "a"])
        self.assertEqual(shell_scan.shell_words('git push "x'), ["git", "push", "x"])

    def test_is_operator(self):
        self.assertTrue(shell_scan.is_operator("&&"))
        self.assertTrue(shell_scan.is_operator("\n"))
        self.assertFalse(shell_scan.is_operator(""))
        self.assertFalse(shell_scan.is_operator("a&"))

    def test_skip_options(self):
        words = ["-c", "k=v", "-C", "d", "--no-pager", "push"]
        self.assertEqual(shell_scan.skip_options(words, 0, {"-c", "-C"}), 5)
        self.assertEqual(shell_scan.skip_options(words, 0), 1)  # k=v is not an option
        self.assertEqual(shell_scan.skip_options(["push"], 0), 0)

    def test_segments_split_commands_and_collect_redirects(self):
        segs = shell_scan.segments(shell_scan.shell_words("a x&&b; c|d >f <<< h"))
        self.assertEqual(segs, [(["a", "x"], [], []), (["b"], [], []), (["c"], [], []),
                                (["d", "f", "h"], ["f"], ["h"])])
        self.assertEqual(shell_scan.segments([]), [([], [], [])])
        self.assertEqual(len(shell_scan.segments(shell_scan.shell_words("a & b || c |& d"))), 4)


class ScanTests(unittest.TestCase):
    def test_plain_command_is_unchanged(self):
        full, main, docs, spans, longest = scan("git add -A && git push")
        self.assertEqual((full, main, docs, spans, longest),
                         ("git add -A && git push", "git add -A && git push", [], [], 0))

    def test_comments_leave_main_but_stay_in_full(self):
        full, main, _, _, _ = scan("echo hi # git push\nls")
        self.assertEqual(full, "echo hi # git push\nls")
        self.assertEqual(main, "echo hi \nls")

    def test_hash_inside_a_word_is_not_a_comment(self):
        for text in ["curl http://x/#frag", "echo a#b", "echo 'a # b'"]:
            with self.subTest(text=text):
                self.assertEqual(scan(text)[1], text)

    def test_comments_kept_on_unsure_lines(self):
        for text in ["echo $(x) # keep", "echo `x` # keep", "x=$((1)) # keep"]:
            with self.subTest(text=text):
                self.assertEqual(scan(text)[1], text)

    def test_ansi_c_strings_are_decoded_for_main_only(self):
        full, main, _, _, _ = scan("git $'\\x70ush'")
        self.assertEqual(full, "git $'\\x70ush'")
        self.assertEqual(main, "git 'push'")
        self.assertEqual(scan("echo $'it\\'s'")[1], "echo 'it'\\''s'")
        self.assertEqual(scan("echo $'a\\nb'")[1], "echo 'a\nb'")
        self.assertEqual(scan('echo $"x" y')[1], 'echo "x" y')

    def test_line_continuations_are_joined(self):
        full, main, _, _, _ = scan("git \\\n  push")
        self.assertEqual((full, main), ("git   push", "git   push"))

    def test_arithmetic_shift_is_not_a_heredoc(self):
        full, main, docs, _, _ = scan("x=$((1<<2))\ngit push")
        self.assertEqual(docs, [])
        self.assertEqual(main, "x=$((1<<2))\ngit push")

    def test_data_heredoc_body_leaves_main_and_is_a_span_in_full(self):
        text = "git commit -F - <<'EOF'\nmsg git push\nEOF\nls"
        full, main, docs, spans, _ = scan(text)
        self.assertEqual(full, text)
        self.assertEqual(main, "git commit -F - <<'EOF'\nls")
        self.assertEqual(docs, [("git commit -F - <<'EOF'\n", "msg git push", True)])
        self.assertEqual(spans, [(24, 41)])
        self.assertEqual(full[24:41], "msg git push\nEOF\n")

    def test_heredocs_that_run_or_are_unclear_stay_in_main(self):
        cases = [
            "cat <<'EOF' | bash\ngit push\nEOF",  # piped on
            "git commit -F - <<'EOF'\ngit push\n",  # delimiter never found
            "cat > notes.md <<EOF\n$(git push)\nEOF",  # unquoted body expands $(...)
            "cat > /tmp/r.sh <<'EOF'\ngit push\nEOF\nbash /tmp/r.sh",  # written, then run
            "bash <<'EOF'\ngit push\nEOF",  # not a data command
        ]
        for text in cases:
            with self.subTest(text=text):
                full, main, docs, spans, _ = scan(text)
                self.assertEqual(spans, [])
                self.assertFalse(any(data for _, _, data in docs))
                self.assertIn("git push", main)

    def test_cat_and_tee_data_heredocs(self):
        for text in ["cat > notes.md <<'EOF'\nrm x\nEOF", "cat >> notes.md <<EOF\nrm x\nEOF",
                     "tee notes.md <<'EOF'\nrm x\nEOF", "cat <<'EOF' > notes.md\nrm x\nEOF"]:
            with self.subTest(text=text):
                _, main, docs, spans, _ = scan(text)
                self.assertNotIn("rm x", main)
                self.assertEqual(len(spans), 1)
                self.assertTrue(docs[0][2])

    def test_two_heredocs_on_one_line_are_not_data(self):
        _, main, docs, spans, _ = scan("cat <<A > x <<B\none\nA\ntwo\nB")
        self.assertEqual(spans, [])
        self.assertEqual([d for _, _, d in docs], [False, False])

    def test_longest_quote(self):
        self.assertEqual(scan("echo 'a b' \"c d\"")[4], 5)
        self.assertEqual(scan("echo 'unterminated")[4], 14)

    def test_here_string_is_not_a_heredoc(self):
        self.assertEqual(scan("cat <<< 'git push'")[2], [])


class BudgetTests(unittest.TestCase):
    def set_limit(self, name, value):
        self.addCleanup(setattr, shell_scan, name, getattr(shell_scan, name))
        setattr(shell_scan, name, value)

    def test_ticks_within_the_budget_pass(self):
        self.set_limit("CHECK_STEPS", 5)
        budget = shell_scan.Budget()
        budget.tick(5)
        with self.assertRaises(shell_scan.CheckTooLarge):
            budget.tick()

    def test_overflow_raises_check_too_large(self):
        self.set_limit("CHECK_STEPS", 0)
        with self.assertRaises(shell_scan.CheckTooLarge):
            shell_scan.Budget().tick()

    def test_deadline_raises_check_too_large(self):
        self.set_limit("CHECK_SECONDS", -1)
        with self.assertRaises(shell_scan.CheckTooLarge):
            shell_scan.Budget().tick(0)

    def test_limits_are_read_when_the_budget_starts(self):
        budget = shell_scan.Budget()
        self.set_limit("CHECK_STEPS", 0)
        budget.tick()  # started with the full budget

    def test_defaults(self):
        self.assertEqual((shell_scan.CHECK_SECONDS, shell_scan.CHECK_STEPS,
                          shell_scan.LONGEST_WORD, shell_scan.LONGEST_COMMAND),
                         (1.5, 400000, 32768, 262144))


class ModuleTests(unittest.TestCase):
    def assertNoIO(self, name, allowed_imports):
        with open(os.path.join(SCRIPTS, name)) as f:
            tree = ast.parse(f.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertLessEqual(imported, allowed_imports)
        calls = {node.func.id for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertFalse(calls & {"open", "print", "exit", "input", "exec", "eval"})
        attributes = {f"{node.value.id}.{node.attr}" for node in ast.walk(tree)
                      if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)}
        self.assertFalse(attributes & {"sys.exit", "sys.stdout", "sys.stderr", "sys.stdin",
                                       "os.system", "os.popen", "os.remove", "os._exit"})

    def test_scanner_has_no_io_no_exit_no_policy(self):
        """The scanner reads text only: no subprocess, files, stdout, exit or config."""
        self.assertNoIO("shell_scan.py", {"os", "re", "shlex", "time"})

    def test_shell_check_has_no_io_and_no_exit(self):
        """ShellCheck only reads the command text and the paths it is given (os.getcwd when no
        cwd is given; sys only for sys.platform). The decision is bash_rules.check()'s."""
        self.assertNoIO("shell_check.py", {"fnmatch", "os", "re", "sys", "shell_scan"})


if __name__ == "__main__":
    unittest.main()
