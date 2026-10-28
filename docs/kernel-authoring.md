# Writing a kernel

A kernel teaches GCAD Studio a modelling language. It is a folder of text files: a manifest, a grammar, and rewrite rules that turn the grammar's syntax tree into GCore, the small functional language every kernel shares. No Python is involved. This guide builds a complete kernel in four files, then lists the details you will need for a larger one.

The bundled kernels are working references: [`parts`](../src/gcad/kernels/parts) (three files, a line-based parts list), [`csg`](../src/gcad/kernels/csg) (S-expressions, three rewrite rules) and [`minscad`](../src/gcad/kernels/minscad) (an OpenSCAD subset with a prelude).

## A kernel in four files

The language: one solid per line, `up` lifts it, `minus` subtracts the solid on its right.

```
# a snowman on a plinth
box 30
ball 12 up 27
ball 8 up 44 minus box 4 up 50
```

**`stack/kernel.toml`** names the language and its file extension:

```toml
id = "stack"
name = "Stack of shapes"
version = "0.1.0"
extensions = [".stack"]
comment = "#"
parser = "lalr"
```

**`stack/grammar.lark`** is a [Lark](https://lark-parser.readthedocs.io/) EBNF grammar. Give every alternative that should become a node an alias (`-> name`); the `?` before `item` lets an alternative without an alias pass its single child through.

```lark
start: item*

?item: "box" NUMBER                 -> box
     | "ball" NUMBER                -> ball
     | item "up" NUMBER             -> up
     | item "minus" item            -> minus

NUMBER: /\d+(\.\d+)?/
COMMENT: /#[^\n]*/
%ignore COMMENT
%ignore /\s+/
```

**`stack/rewrite.toml`** gives each tree name a GCore template. `$1`, `$2` are the node's children in order, after Lark has dropped the quoted keywords and punctuation:

```toml
[rules]
start = "$*"
box = "(box $1 :center true)"
ball = "(sphere $1)"
up = "(translate [0 0 $2] $1)"
minus = "(difference $1 $2)"

[tokens]
NUMBER = "number"
```

**`stack/examples/snowman.stack`** holds the sample above; `gcad kernel check` runs every file in `examples/`.

Try it:

```bash
gcad kernel check stack
GCAD_KERNEL_PATH=. gcad eval stack/examples/snowman.stack --dump gcore
GCAD_KERNEL_PATH=. gcad export stack/examples/snowman.stack -o snowman.stl
```

(In PowerShell, set the variable with `$env:GCAD_KERNEL_PATH = "."` first.) `--dump gcore` shows what your rules produced:

```scheme
(box 30 :center true)
(translate [0 0 27] (sphere 12))
(difference (translate [0 0 44] (sphere 8)) (translate [0 0 50] (box 4 :center true)))
```

## Rewrite templates

A template is GCore text. Symbols starting with `$` and a digit, or `$*`, are placeholders:

| Placeholder | Becomes |
|---|---|
| `$n` | child n, rewritten by its own rule (a token is converted by `[tokens]`) |
| `$n*` | the children of child n, each rewritten, spliced in place |
| `$*` | all children of this node, spliced |
| `$n:str` / `$n:sym` / `$n:num` | the source text of child n as a string, symbol or number |
| `:$n` | a keyword (named argument) named by child n's text |

- An optional part of a rule (`[ ... ]`) that did not match is an absent child; its placeholder produces nothing. So `call = "($1 $2*)"` works for `f()` and `f(1, 2)` alike.
- A template may contain several forms, which are spliced into the parent (`named_arg = ":$1 $2"`), or none, which removes the node (`empty = ""`).
- Rules that only collect a list (`arguments: argument ("," argument)*`) still need a rule; `"$*"` is the usual one.
- Template forms that are not placeholders are copied, and carry the source position of the node, so errors point at the user's code.

`[tokens]` maps a terminal to `number`, `string` (JSON-style escapes), `symbol`, `keyword` (a leading `:` is dropped) or `text` (the raw text as a string). Unlisted terminals are guessed from their names: `NUMBER`, `INT` and `FLOAT` are numbers, names containing `STRING` are strings, everything else is a symbol.

## GCore in brief

What templates can produce: `(define name expr)`, `(define (f x (y 2)) body)`, `(module (name params) body)` where shape arguments arrive as `children`, `(fn (x) body)`, `(let ((a 1)) body)`, `(if c a b)`, `(for ((i list)) body)`, `(and ...)`, `(or ...)`, named arguments `:name value`, vectors `[x y z]`. The geometry built-ins are listed in [SPEC §5.3](SPEC.md#53-geometry-graph); `(range a b [step])` is inclusive. A body with several shape expressions is their union.

## A prelude

If your language uses different names for GCore built-ins, or needs helper functions, put GCore definitions in `prelude.gcore`. They are evaluated in a scope of their own, between the built-ins and the user's program, so a program can still redefine any of them. `minscad`'s prelude, for example, has `(define cube box)`.

## Languages whose files refer to themselves

Some languages are not trees but graphs: an ISO 10303-21 (STEP) file is a list of numbered instances, each referring to others by number, in any order. The [`iso10303`](../src/gcad/kernels/iso10303) kernel shows how to handle one with data only.

- **`letrec`** binds names in any order: `(letrec ((b (+ a 1)) (a 2)) b)`. Each binding is evaluated once, when it is first used; a name bound twice and a cycle of references (`a -> b -> a`) are errors that name them. Without a body, `letrec` gives the list of every binding's value, in order, which is how a kernel hands "all the instances" to its prelude. A rewrite rule such as `instance = "($1 (record $1:str $2))"` turns each instance into a binding.
- **`(record item...)`** is a list that remembers where it was written. **`(at record body...)`** makes the geometry and the diagnostics of `body` point at that record's place in the user's file, even when the code that runs is in the prelude. So a prelude can be an interpreter of the user's data, and clicking a face still selects the source that made it. `(warn "...")`, `(info "...")` and `(fail "...")` report at that place too.
- **`(resolve "name" default)`** gives the value a name has, the name given as a string: a prelude can dispatch on data, as in `((resolve (str "solid/" keyword) unknown) record)`. Also for data: `(apply f list)`, `(contains list x)`, `(split s sep)`, `(upper s)`, `(lower s)`, `(starts_with s prefix)`, `(parse_number s)` and `(parse_value s)`, which reads a GCore literal without evaluating anything.
- **`(import_source "step")`** hands the source file itself to a back-end that can read it: an `import` node in the graph, which keeps the file by its SHA-256. A record whose first item is a non-empty string is *named* by it (`"#12"`); when a back-end labels geometry with the source entity it came from (OpenCASCADE knows which `#n` each face of an imported STEP solid was read from), the studio points that face at the named record, so clicking a face of an imported B-rep selects its `ADVANCED_FACE` line.
- **`(position list x)`** is the index of the first element equal to `x`, or -1. Two parallel lists made once are a lookup table: interpreting a file of thousands of instances, a prelude should not scan a list per instance.
- **`(annotate id label :kind :lines :triangles :color :views :targets :links :details :camera)`** reports what the model says about its geometry: a note, a dimension, a tolerance, a saved view. Annotations travel beside the graph, never in it, so they change no hash and no back-end sees them; the studio draws `:lines` (polylines of `[x y z]` points) and `:triangles` (three points each) over the model in its annotation panel, with a check box each. `:targets` are records (or their names) the annotation applies to, highlighted when its row is selected; `:links` are ids of related annotations (a tolerance and the callout that presents it); `:details` are `[name value]` pairs; `:views` are ids of annotations of kind `"view"`, whose `:camera` is `[position direction up]` and whose `:links` are the annotations they show. `gcad eval --dump annotations` lists them. The `iso10303` kernel's PMI section is the worked example.
- **String decoders**: `[tokens] STRING = "string:iso10303-21"` reads a terminal with the named decoder instead of JSON escapes.
- **A semantic checker**: `checker = "iso10303-21"` in `kernel.toml` runs a checker the engine provides after parsing, for what a grammar cannot decide (here: the file against its EXPRESS schemas). Its errors are reported and make `gcad eval` exit non-zero, but the model is still built.

A language that is specified in a standard keeps its own notation there. `gcad grammar import --notation wsn productions.txt --start NAME` converts Wirth Syntax Notation to a Lark grammar (Earley, character level), which is how the `iso10303` kernel's hand-written grammar is checked against the standard's productions; see [the design note](design/iso10303-kernel.md#1-notation-grammar-and-tool).

## Choosing a parser

`parser = "lalr"` is fast and most languages fit. Reduce/reduce conflicts are errors when the grammar is compiled; shift/reduce conflicts are resolved by shifting, which is what makes a dangling `else` bind to the nearest `if`. `parser = "earley"` accepts any context-free grammar, including ambiguous ones, at some cost in speed. `start` in `kernel.toml` picks a start rule other than `start`.

## Checking and debugging

- `gcad kernel check <folder>` compiles the grammar, reports every tree name without a rewrite rule (an error) and every rule that matches nothing (a warning), reads the prelude, and evaluates each example.
- `gcad eval <file> --dump gcore` shows the rewrite result; `--dump graph` shows the geometry graph.
- `gcad kernel describe <id or folder>` prints the keywords, operators and token classes an editor uses for highlighting.
- Quoted words in the grammar are keywords, so a user cannot also use them as names: in `parts`, a part cannot be called `d` because `cylinder d 8` uses `"d"`.
