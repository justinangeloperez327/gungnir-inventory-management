# Gungnir structured syntax and Windows checkout

This application uses **profile=structured** in `.gungnir-project`.
The structured compiler is required for model configuration, imports,
relationships, typed controllers, migrations and native C++ generation.

## String literals and model metadata

In the structured Gungnir language, **both single- and double-quoted strings
are valid**. For example:

```gnr
model Category {
    table = "categories";
    fillable = ["code", "name"];
    timestamps = true;

    string code;
    string name;
}
```

The equivalent declaration using `'categories'`, `'code'` and `'name'`
is also valid. Use a consistent style within each file, but do not mechanically
rewrite working structured `.gnr` sources to address a legacy-compiler error.

The legacy `CompatibilityTranspiler` rejects multi-character single-quoted
literals (GNR0904) and structured model metadata including `fillable`
(GNR1330). These diagnostics can indicate **incorrect project-profile
selection**, not an invalid structured model declaration.

## Windows line endings

The v1.0.2 CLI identifies `profile=structured` using an LF-sensitive check.
On Windows, CRLF checkout of `.gungnir-project` can silently switch
`gungnir build` to compatibility compilation.

`.gitattributes` enforces LF for the project marker and `.gnr` sources.
Newer Gungnir source also fixes the CLI to read both LF and CRLF profiles.

To verify checkout formatting and compilation:

```powershell
git check-attr text eol -- .gungnir-project app/models/Category.gnr
gungnir --version
gungnirc --version
gungnirc --project . --check
gungnirc --project . --verify-ir
gungnir build
```

`git check-attr` should report `text: set` and `eol: lf` for both files.
The installed Gungnir **Application SDK** must supply the CLI, compiler,
SQLite, password hashing, headers and matching native libraries.

If this is an existing Windows checkout whose marker already has CRLF,
normalize it once with:

```powershell
git add --renormalize .gungnir-project
git checkout-index --force -- .gungnir-project
```

Alternatively, normalize just the file as UTF-8 without BOM using
PowerShell's `[IO.File]` APIs. Do not edit generated `.gungnir/` C++ by hand.

## Acceptance criteria

The project must pass both `gungnirc --project . --check` and
`gungnirc --project . --verify-ir`, native `gungnir build`, migrations,
and authenticated HTTP smoke tests with the pinned Gungnir Application SDK.
Build success on Linux alone does not establish Windows compatibility.
