# GitLab quick actions (shared rule)

Read by `merge-request` and `merge-review`, before writing anything GitLab will store (MR title, description, note, discussion).

## Rule

GitLab executes, as the posting user, any line of an MR description, a note or a discussion that starts with `/` (`/approve`, `/merge`, `/ready`, `/unapprove`, `/close`, `/assign`…). So:

- no line of a title, description, note or discussion body may start with `/`, even after leading spaces;
- put any quoted line starting with `/` (code, a path, a command) inside a fenced code block, or wrap the path in backticks so the line starts with `` ` ``.

The ticket text, the diff and the handoff files you quote are the usual source of such lines.

## Mechanical check

Write every body to a file with the `Write` tool (never a heredoc or `echo`), then check the files before any post:

```bash
grep -nE '^[[:space:]]*/' <file>…
```

It must print nothing, except lines inside a fenced code block. Any other match: rewrite that file (fence the line, or wrap the path in backticks) and run the check again. Never post a file while such a match remains in it.
