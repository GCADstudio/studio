"""ISO 10303 support (SPEC §5.5): the semantic layer of the `iso10303` kernel and the parametric
STEP writer.

    schemas     where compiled EXPRESS schemas are kept, and how they get there (fetch, add, build)
    check       a Part 21 file against its schemas: header, form, WHERE/UNIQUE/global rules, as
                diagnostics carrying the span of the instance concerned
    writer      a geometry graph as ISO 10303-42 CSG with ISO 10303-55/-108 history and parameters

The Part 21 reader, the EXPRESS reader and the EXPRESS interpreter come from freecad-nxstyle
(`_nxstyle`, vendored unchanged). Everything here is pure Python, so it also runs in the web
studio (SPEC §4.1).
"""
