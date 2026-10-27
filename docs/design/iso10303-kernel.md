# ISO 10303 kernel: design note

Status: proposed with the M14 milestone (SPEC §12.3), on branch `claude/iso10303-kernel-72ab6c`. This note is the design record for the `iso10303` kernel: opening ISO 10303-21 exchange files in the studio, checking them against their EXPRESS schemas, editing their parameters, and writing the studio's own models back as parametric ISO 10303.

No text of any ISO standard is reproduced here or anywhere in the repository. Clauses are cited by number; productions and entities by name only.

## 1. Notation, grammar and tool

SPEC §2 (rows D12 and D13) took two lessons from the [CS.SE question on BNF alternatives](https://cs.stackexchange.com/questions/127499): notations, mathematical grammar classes and parser tools are different things, and a parser's grammar takes the form its parser technology dictates. FR-K1 then fixed Lark EBNF as the studio's grammar notation. ISO 10303 is the first case where a kernel implements a language that someone else has *specified* in a notation of its own, so the lesson has to be applied in its second half too. This section does that.

### 1.1 Three layers, kept apart

| Layer | What it is | For this kernel |
|---|---|---|
| Grammar *class* | A mathematical object: a set of rules generating a language. Context-free grammars are one class; Van Wijngaarden two-level grammars are a strictly larger one, so large that parsing with them is undecidable in general. | The *syntax* of the exchange structure (ISO 10303-21) is context-free, and its token level is regular. The *language of valid files* is not: which `#n` exist, how many attributes an entity has and which rules hold depend on the rest of the file and on an EXPRESS schema. A two-level grammar could in principle state some of that, at the price of a parser nobody can guarantee terminates. We do not go there (§1.3). |
| *Notation* | A way to write a grammar of some class down: BNF, ABNF, ISO 14977 EBNF, W3C EBNF, Wirth Syntax Notation (WSN). | ISO 10303-11 and ISO 10303-21 both write their syntax in WSN (§1.2). WSN is the reference notation for this kernel. |
| *Tool dialect* | A parser generator's own input language, with features only a tool needs (ANTLR's embedded actions, Lark's `->` aliases, `?` inlining, terminal priorities, `%ignore`). | Lark EBNF, as for every grammar kernel (FR-K1, [`grammar.py`](../../src/gcad/kernels/grammar.py)). The `iso10303` grammar is written in it on purpose: it is what the studio loads at run time with no generation step, and the aliases are what [`rewrite.toml`](../../src/gcad/kernels/iso10303/rewrite.toml) keys on. |

So "the Part 21 grammar" means two different things, and this note keeps them apart: the **reference** is the WSN in ISO 10303-21; the **implementation** is [`src/gcad/kernels/iso10303/grammar.lark`](../../src/gcad/kernels/iso10303/grammar.lark).

### 1.2 Specification versus parser

The second CS.SE answer distinguishes a grammar written to drive a parser, whose form the parser technology dictates, from a grammar written in a specification, where the document defines its own notation. ISO 10303-21 and ISO 10303-11 are specifications, and each defines its notation:

| Standard (edition in the reference set) | Notation defined in | Productions in |
|---|---|---|
| ISO 10303-21:2002 (exchange structure, clear text encoding) | 5.1, and annex B (WSN defined in itself, Table B.1) | Table 1 (5.2, subsets of the basic alphabet), Table 2 (5.4, tokens), Table 3 (5.5, exchange structure), Table 4 (6.3.3, string control directives). Token separators are prose (5.6), print control directives are clause 11, and the handling of line breaks is annex A. |
| ISO 10303-11:1994 (EXPRESS) | 6.1 (syntax of the specification) and 6.2 (special character notation) | Annex A: A.1 tokens (A.1.1 keywords, A.1.2 character classes, A.1.3 lexical elements, A.1.4 remarks, A.1.5 interpreted identifiers), A.2 grammar rules, A.3 cross-reference listing. |

The kernel's Lark grammar is therefore an implementation of the Part 21 productions, never a replacement for them. Each Lark rule traces to a WSN production, and each place where the two differ is listed with its reason:

| WSN production (ISO 10303-21:2002) | Lark rule or terminal in `grammar.lark` | Deviation and reason |
|---|---|---|
| `EXCHANGE_FILE` (Table 3) | `exchange_file`, `data` | The repetition of data sections is a rule of its own, `data`, so the rewrite can splice every section's instances into one binding list. Same language. |
| `HEADER_SECTION`, `HEADER_ENTITY_LIST` | `header_section` | The optional list is written as `header_entity*` after the three mandatory entities. Same language. That the three are `FILE_DESCRIPTION`, `FILE_NAME` and `FILE_SCHEMA`, in that order, is clause 8, checked by the semantic layer. |
| `HEADER_ENTITY`, `SIMPLE_RECORD` | `header_entity`, `simple_record` | None. |
| `PARAMETER_LIST`, `PARAMETER` | `parameter_list`, `?parameter` | `?` inlines the choice, so the tree holds the chosen parameter directly. Same language. |
| `TYPED_PARAMETER` | `typed_parameter` | None. |
| `UNTYPED_PARAMETER` | `?untyped_parameter` with aliases `unset` (`$`) | Each alternative becomes its own tree or token, so each gets its own rewrite. Same language. |
| `OMITTED_PARAMETER` | `omitted` | Alias only. |
| `LIST` | `list` | None. |
| `DATA_SECTION` | `data_section`, `section_parameters`, `instances` | The production as printed in Table 3 ends without its full stop, which WSN requires (Table B.1); read as the obvious intent. See §1.6. |
| `ENTITY_INSTANCE_LIST`, `ENTITY_INSTANCE` | `instances`, `?entity_instance` | Same language. |
| `SIMPLE_ENTITY_INSTANCE`, `COMPLEX_ENTITY_INSTANCE` | `simple_instance`, `complex_instance` | Shorter names. |
| `SUBSUPER_RECORD`, `SIMPLE_RECORD_LIST` | `subsuper_record` | `simple_record+`. Same language. |
| `KEYWORD`, `USER_DEFINED_KEYWORD`, `STANDARD_KEYWORD` (Table 2) | terminal `KEYWORD` | Lexical rules become one regular terminal. Case-sensitive, as Table 1 defines `UPPER` (EXPRESS identifiers are case-insensitive; Part 21 writes them in capitals, 10.2). |
| `SIGN`, `INTEGER`, `REAL` | terminals `INTEGER`, `REAL` | `REAL` has a higher terminal priority, so `12.` is never read as `12` followed by a stray full stop. |
| `STRING`, `NON_Q_CHAR`, `CONTROL_DIRECTIVE` and Table 4 | terminal `STRING` | One regular terminal covering the control directives of Table 4 and the print control directives of clause 11; decoded by the `iso10303-21` string decoder (§1.4). Accepts line breaks inside the string (annex A). |
| `ENTITY_INSTANCE_NAME` | terminal `INSTANCE` | Shorter name. |
| `ENUMERATION`, `HEX`, `BINARY` | terminals `ENUMERATION`, `BINARY` | `BINARY` accepts line breaks inside (annex A). |
| Table 1 (`SPACE`, `DIGIT`, `LOWER`, `UPPER`, `SPECIAL`, ...) | character classes inside the terminals | Not separate rules: Lark terminals are regular expressions. Characters outside the basic alphabet (5.2) are accepted inside strings, as most writers emit them, and reported by the semantic layer as a warning. |
| 5.6 token separators, clause 11 print control directives, annex A line breaks | `%ignore` of white space, `/* */` comments, `\N\` and `\F\` | A line break *inside* a token other than a string or a binary is not accepted: annex A allows it, but no writer we know of does it and a Lark terminal cannot skip characters. It is a syntax error with a precise position. |

The same reasoning, one level up, applies to EXPRESS. Its WSN is ambiguous by design where only meaning can decide: annex A.1.5 makes `entity_ref`, `type_ref`, `function_ref` and several others the same `simple_id`, and which one a name is depends on the schema's symbol table. The EXPRESS reader we reuse ([`gcad/iso10303/express.py`](../../src/gcad/iso10303/express.py), from freecad-nxstyle) is a hand-written recursive-descent parser that resolves those names against its dictionary; it does not pretend a context-free grammar can.

### 1.3 What a context-free grammar cannot hold, and where it lives instead

| Constraint | Source | Where it is enforced |
|---|---|---|
| `#n` is defined exactly once across all data sections | 10303-21 clauses 9 and 10 | Engine: `letrec` rejects a second binding of the same name with both spans (§3.1). The semantic layer reports it too, against the instance list. |
| `#12` may be used before `#12=` | clause 9 (forward references allowed) | Engine: `letrec` bindings are order-free. |
| A referenced `#n` exists | clause 10 | Engine: a dangling name fails with its span; semantic layer: `express.check` reports the reference. |
| No reference cycles in what we evaluate | not a Part 21 rule; a limit of evaluating values | Engine: `letrec` detects the cycle and names it (`#3 -> #5 -> #3`). Part 21 does not forbid cyclic instance graphs; GCAD cannot build a value from one, and says so. |
| The header has `FILE_DESCRIPTION`, `FILE_NAME`, `FILE_SCHEMA` in that order | clause 8 | Semantic layer: `express.check_header`. |
| An instance's keyword names an entity of the schema, with the right number and types of attributes, `$` only where OPTIONAL, `*` only where derived | clauses 10.2 and 10.3; ISO 10303-11 | Semantic layer: `express.check` against the schema the header names. |
| WHERE rules, UNIQUE rules, global rules, INVERSE cardinalities | ISO 10303-11 9.2.2, 9.2.1.3, 9.6 | Semantic layer: `express_eval.rules`, which runs the schemas' own functions. |
| A string is at most 32769 bytes as stored | 10303-21 6.3.3 | Semantic layer (the reader). |

None of these is squeezed into the grammar. The grammar decides only whether a file is a well-formed exchange structure; everything that needs another part of the file or a schema runs after parsing, on the parse result, and reports with the span of the instance concerned.

### 1.4 FR-K8 and the WSN: derive or hand-write?

FR-K8 (Could) imports ISO 14977 and W3C EBNF grammars by converting them to Lark. A WSN importer belongs there: WSN is close to ISO 14977 (`=`, `|`, `[ ]`, `{ }`, `( )`, quoted terminals, a full stop ending each production).

Decision: **the Part 21 grammar is hand-written, and the WSN is used to verify it, on the user's own copy of the standard.**

- Deriving the grammar mechanically from the WSN at build time would need the WSN in the repository, which the disclosure rule forbids (the productions are the standard's text). Deriving it at run time from the user's PDF would make the kernel depend on a file most users do not have.
- The mechanical derivation would still need every deviation of §1.2 applied by hand: terminals for lexical rules, the line-break rule, LALR shape, aliases for the rewrite.
- So the committed grammar is written by hand with the traceability table above, and `gcad grammar import --notation wsn` (FR-K8, built in this milestone) converts WSN to Lark. `tests/test_iso10303_wsn.py` extracts Tables 2 to 4 from a copy of ISO 10303-21 when one is pointed to by `GCAD_ISO10303_21`, converts them, and checks both grammars accept and reject the same sentences: sentences generated from each grammar are parsed by the other, together with the kernel's fixtures and negative controls. Without the copy the test is skipped and says why; the copy never enters the repository.

### 1.5 Why this matters to users

A file opened in the studio is checked against a grammar. If that grammar's relation to the standard is not written down, "this file conforms to ISO 10303-21" is only an assertion about some parser. With the traceability table, the deviation list and the differential test against the WSN, a user can see which production every rule implements, what was deliberately done differently and why, and re-run the comparison on their own copy of the standard. The same holds one layer up: a file is "valid against AP242" only for the rules that were evaluated, and the semantic layer reports how many were evaluated, how many were violated and how many could not be decided (§3.3).

### 1.6 Errors found in the notation

Checked against the 14 errors in the standards already recorded with freecad-nxstyle (parametric-step page, "Errors in the standards themselves"); this one is new:

15. **10303-21:2002 Table 3, `DATA_SECTION`.** The production ends without the full stop that WSN requires after every production (annex B). A mechanical WSN reader must special-case it; read literally, `DATA_SECTION` and `ENTITY_INSTANCE_LIST` run together into one malformed production.
