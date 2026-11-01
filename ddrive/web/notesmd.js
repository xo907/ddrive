// Notes in Markdown: what a note looks like in Preview (app.js, noteEditor), with code in colours.
//
// Nothing of a note is ever taken as HTML: every bit of its text is escaped, and only the tags made
// here (headings, lists, code, links to http(s) and mail addresses) are put around it. So a note,
// even one shared to you or written by someone else, can't run anything in the dashboard.
// Code is coloured by a small tokenizer for the common languages (no library, nothing fetched).
(function () {
  "use strict";

  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  // ---------------------------------------------------------------- code colours
  const words = (s) => new Set(s.split(/\s+/).filter(Boolean));
  const C_LIKE_LIT = "true false null undefined NaN Infinity this super self nil None True False";
  const LANGS = {
    js: { kw: words(`async await break case catch class const continue debugger default delete do else export extends finally
                     for from function get if import in instanceof let new of return set static switch throw try typeof var
                     void while with yield as`),
          lit: words(C_LIKE_LIT), line: "//", block: ["/*", "*/"], str: "\"'`", regex: true },
    ts: { kw: words(`abstract any as async await boolean break case catch class const constructor continue declare default
                     delete do else enum export extends finally for from function get if implements import in infer
                     instanceof interface is keyof let module namespace never new number object of private protected public
                     readonly return satisfies set static string switch symbol throw try type typeof unknown var void while yield`),
          lit: words(C_LIKE_LIT), line: "//", block: ["/*", "*/"], str: "\"'`", regex: true },
    py: { kw: words(`and as assert async await break class continue def del elif else except finally for from global if import
                     in is lambda match case nonlocal not or pass raise return try while with yield print`),
          lit: words("True False None self cls"), line: "#", str: "\"'", triple: true, deco: true },
    sh: { kw: words(`if then else elif fi for while until do done case esac function in select return exit export local
                     readonly unset shift source alias echo cd sudo set`),
          lit: words("true false"), line: "#", str: "\"'", vars: "$", flags: true },
    ps: { kw: words(`if else elseif foreach for while do until switch function param return break continue try catch finally
                     throw begin process end in`),
          lit: words("$true $false $null"), line: "#", block: ["<#", "#>"], str: "\"'", vars: "$", flags: true, nocase: true, cmdlets: true },
    c: { kw: words(`auto break case char const continue default do double else enum extern float for goto if inline int long
                    register return short signed sizeof static struct switch typedef union unsigned void volatile while
                    class namespace template typename public private protected virtual override new delete using try catch
                    throw nullptr bool true false std include define ifdef ifndef endif pragma`),
         lit: words("NULL nullptr true false this"), line: "//", block: ["/*", "*/"], str: "\"'", pre: true },
    cs: { kw: words(`abstract as async await base bool break byte case catch char checked class const continue decimal default
                     delegate do double else enum event explicit extern finally fixed float for foreach goto if implicit in int
                     interface internal is lock long namespace new object operator out override params private protected
                     public readonly record ref return sbyte sealed short sizeof stackalloc static string struct switch throw
                     try typeof uint ulong unchecked unsafe ushort using var virtual void volatile while get set init yield`),
          lit: words("true false null this"), line: "//", block: ["/*", "*/"], str: "\"'", deco: "[" },
    java: { kw: words(`abstract assert boolean break byte case catch char class const continue default do double else enum
                       extends final finally float for if implements import instanceof int interface long native new package
                       private protected public return short static strictfp super switch synchronized throw throws transient
                       try var void volatile while record sealed permits yield
                       fun val when object companion data suspend override open internal lateinit by is typealias`),
            lit: words("true false null this"), line: "//", block: ["/*", "*/"], str: "\"'", deco: true },
    go: { kw: words(`break case chan const continue default defer else fallthrough for func go goto if import interface map
                     package range return select struct switch type var`),
          lit: words("true false nil iota"), line: "//", block: ["/*", "*/"], str: "\"'`" },
    rs: { kw: words(`as async await break const continue crate dyn else enum extern fn for if impl in let loop match mod move mut
                     pub ref return static struct super trait type unsafe use where while`),
          lit: words("true false self Self None Some Ok Err"), line: "//", block: ["/*", "*/"], str: "\"", deco: "#" },
    swift: { kw: words(`associatedtype class deinit enum extension fileprivate func import init inout internal let open operator
                        private protocol public rethrows static struct subscript typealias var break case continue default
                        defer do else fallthrough for guard if in repeat return switch where while as catch is throw throws
                        try async await some any`),
             lit: words("true false nil self Self"), line: "//", block: ["/*", "*/"], str: "\"", deco: true },
    php: { kw: words(`abstract and array as break callable case catch class clone const continue declare default do echo else
                      elseif empty enddeclare endfor endforeach endif endswitch endwhile extends final finally fn for foreach
                      function global goto if implements include include_once instanceof insteadof interface isset list match
                      namespace new or print private protected public readonly require require_once return static switch throw
                      trait try unset use var while xor yield`),
           lit: words("true false null TRUE FALSE NULL"), line: "//", block: ["/*", "*/"], str: "\"'", vars: "$", hashline: true },
    rb: { kw: words(`alias and begin break case class def defined? do else elsif end ensure for if in module next not or redo
                     rescue retry return then undef unless until when while yield require puts attr_accessor`),
          lit: words("true false nil self"), line: "#", str: "\"'", vars: "@" },
    sql: { kw: words(`select from where and or not insert into values update set delete create table alter drop index view join
                      inner left right outer full on as group by order having limit offset distinct union all case when then
                      else end primary key foreign references default null is in like between exists returning with begin
                      commit rollback transaction int integer text varchar boolean date timestamp serial unique asc desc count`),
           lit: words("true false null"), line: "--", block: ["/*", "*/"], str: "'\"", nocase: true },
    json: { kw: words(""), lit: words("true false null"), str: "\"", keys: true, line: "//", block: ["/*", "*/"] },
    yaml: { kw: words(""), lit: words("true false null yes no on off ~"), line: "#", str: "\"'", keys: true, nocase: true },
    toml: { kw: words(""), lit: words("true false"), line: "#", str: "\"'", keys: true, sections: true },
    css: { css: true },
    html: { markup: true },
    diff: { diff: true },
    docker: { kw: words(`from run cmd label expose env add copy entrypoint volume user workdir arg onbuild stopsignal healthcheck
                         shell as`),
              lit: words(""), line: "#", str: "\"'", vars: "$", nocase: true },
  };
  const ALIAS = { javascript: "js", jsx: "js", mjs: "js", cjs: "js", node: "js", typescript: "ts", tsx: "ts", python: "py",
                  py3: "py", bash: "sh", shell: "sh", zsh: "sh", console: "sh", shellsession: "sh", powershell: "ps",
                  ps1: "ps", pwsh: "ps", h: "c", cpp: "c", "c++": "c", cc: "c", hpp: "c", cxx: "c", objc: "c",
                  csharp: "cs", "c#": "cs", kotlin: "java", kt: "java", kts: "java", scala: "java", dart: "java", golang: "go",
                  rust: "rs", ruby: "rb", jsonc: "json", json5: "json", yml: "yaml", ini: "toml", cfg: "toml", conf: "toml",
                  scss: "css", less: "css", xml: "html", svg: "html", vue: "html", htm: "html", xhtml: "html", patch: "diff",
                  dockerfile: "docker", mysql: "sql", postgres: "sql", postgresql: "sql", sqlite: "sql", plsql: "sql" };
  const NAMES = { js: "JavaScript", ts: "TypeScript", py: "Python", sh: "Shell", ps: "PowerShell", c: "C / C++", cs: "C#",
                  java: "Java / Kotlin", go: "Go", rs: "Rust", swift: "Swift", php: "PHP", rb: "Ruby", sql: "SQL", json: "JSON",
                  yaml: "YAML", toml: "TOML / INI", css: "CSS", html: "HTML / XML", diff: "Diff", docker: "Dockerfile" };

  const span = (cls, text) => `<span class="tk-${cls}">${esc(text)}</span>`;

  function langOf(name, code) {
    const n = String(name || "").toLowerCase();
    if (LANGS[n]) return n;
    if (ALIAS[n]) return ALIAS[n];
    if (n) return "";                                    // (a language not known here: no colours rather than wrong ones)
    return guess(code);
  }

  /** A language for a block that doesn't say (a guess, from things only that language writes). */
  function guess(code) {
    const t = code.trim();
    if (/^[{[]/.test(t)) { try { JSON.parse(t); return "json"; } catch { /* not JSON */ } }
    if (/^<(!doctype|html|\?xml|[a-z][\w-]*[\s>])/i.test(t)) return "html";
    if (/^#!.*\b(ba|z)?sh\b/.test(t) || /^\$ \S/m.test(t)) return "sh";
    if (/^\s*(def |class \w+.*:\s*$|from \w[\w.]* import |import \w+\s*$|if __name__)/m.test(t)) return "py";
    if (/^\s*(select|insert into|update \w+ set|create table|delete from)\b/im.test(t)) return "sql";
    if (/^\s*(package \w+|func \w+\(.*\{)/m.test(t)) return "go";
    if (/^\s*(fn \w+|let mut |use \w+::|impl\b)/m.test(t)) return "rs";
    if (/^\s*(#include\s*<|int main\s*\()/m.test(t)) return "c";
    if (/^\s*(public |private )?(static )?(class|interface) \w+|System\.out\./m.test(t)) return "java";
    if (/\b(Get|Set|New|Write)-[A-Z]\w+|^\s*\$\w+\s*=/m.test(t)) return "ps";
    if (/^\s*(const|let|var|function|import|export)\b|=>|console\.log/m.test(t)) return "js";
    if (/^[\w.-]+\s*\{[^}]*:[^}]*;/m.test(t)) return "css";
    if (/^\s*[\w-]+:\s*\S/m.test(t) && !/[;{}]/.test(t)) return "yaml";
    return "";
  }

  function highlightGeneric(code, L) {
    let out = "", i = 0;
    const n = code.length;
    const kwOf = (w) => (L.nocase ? w.toLowerCase() : w);
    let lineStart = true;                                // (only blanks so far on this line)
    while (i < n) {
      const c = code[i], rest = code.slice(i, i + 3);
      // comments
      if (L.block && code.startsWith(L.block[0], i)) {
        const e = code.indexOf(L.block[1], i + L.block[0].length);
        const j = e < 0 ? n : e + L.block[1].length;
        out += span("c", code.slice(i, j)); i = j; continue;
      }
      if ((L.line && code.startsWith(L.line, i) && (L.line !== "#" || lineStart || /\s/.test(code[i - 1] || " ")))
          || (L.hashline && c === "#")) {
        if (!(L.line === "#" && L.vars === "$" && code[i - 1] === "$")) {
          const e = code.indexOf("\n", i);
          const j = e < 0 ? n : e;
          out += span("c", code.slice(i, j)); i = j; continue;
        }
      }
      // preprocessor (C) and decorators / attributes
      if (L.pre && c === "#" && lineStart) {
        const e = code.indexOf("\n", i); const j = e < 0 ? n : e;
        out += span("m", code.slice(i, j)); i = j; continue;
      }
      if (L.deco && ((L.deco === true && c === "@") || (L.deco === "#" && c === "#" && code[i + 1] === "[")
                     || (L.deco === "[" && c === "[" && lineStart))) {
        const m = /^(@[\w.]+|#!?\[[^\]\n]*\]|\[[A-Z]\w*(\([^)\n]*\))?\])/.exec(code.slice(i));
        if (m) { out += span("m", m[0]); i += m[0].length; lineStart = false; continue; }
      }
      // TOML / INI [section]
      if (L.sections && c === "[" && lineStart) {
        const m = /^\[[^\]\n]*\]/.exec(code.slice(i));
        if (m) { out += span("t", m[0]); i += m[0].length; lineStart = false; continue; }
      }
      // Python's string prefixes (f"…", rb'…') go with their string
      if (L.triple && /[rbfuRBFU]/.test(c) && !/[\w]/.test(code[i - 1] || "")) {
        const m = /^[rbfuRBFU]{1,2}(?=["'])/.exec(code.slice(i));
        if (m) { out += `<span class="tk-s">${esc(m[0])}</span>`; i += m[0].length; continue; }
      }
      // strings
      if (L.str && L.str.includes(c)) {
        let j, q = c;
        if (L.triple && (rest === '"""' || rest === "'''")) {
          const e = code.indexOf(rest, i + 3);
          j = e < 0 ? n : e + 3;
        } else {
          j = i + 1;
          while (j < n && code[j] !== q) {
            if (code[j] === "\\" && q !== "`") j++;
            else if (code[j] === "\n" && q !== "`") break;
            j++;
          }
          j = Math.min(n, j + 1);
        }
        const s = code.slice(i, j);
        const isKey = L.keys && /^\s*:/.test(code.slice(j, j + 40)) && !/^\s*::/.test(code.slice(j));
        out += span(isKey ? "p" : "s", s); i = j; lineStart = false; continue;
      }
      // numbers
      if (/[0-9]/.test(c) && !/[\w$]/.test(code[i - 1] || "")) {
        const m = /^(0x[\da-fA-F_]+|0b[01_]+|0o[0-7_]+|\d[\d_]*(\.\d[\d_]*)?([eE][+-]?\d+)?)[a-zA-Z]*/.exec(code.slice(i));
        out += span("n", m[0]); i += m[0].length; lineStart = false; continue;
      }
      // variables ($x, ${x}, @x)
      if (L.vars && c === L.vars && /[\w{(]/.test(code[i + 1] || "")) {
        const m = /^.(\{[^}\n]*\}|\([^)\n]*\)|[\w:]+)/.exec(code.slice(i));
        if (m) {
          const low = m[0].toLowerCase();
          out += span(L.lit.has(low) ? "l" : "v", m[0]); i += m[0].length; lineStart = false; continue;
        }
      }
      // command-line flags (-x, --name, -Param)
      if (L.flags && c === "-" && /\s/.test(code[i - 1] || " ") && /[A-Za-z-]/.test(code[i + 1] || "")) {
        const m = /^--?[A-Za-z][\w-]*/.exec(code.slice(i));
        if (m) { out += span("p", m[0]); i += m[0].length; lineStart = false; continue; }
      }
      // words
      if (/[A-Za-z_]/.test(c)) {
        const cmdlet = L.cmdlets && /^[A-Za-z]+-[A-Za-z]\w*/.exec(code.slice(i));   // (PowerShell's Get-ChildItem)
        const w = cmdlet ? cmdlet[0] : /^[A-Za-z_]\w*[?!]?/.exec(code.slice(i))[0];
        const after = code.slice(i + w.length, i + w.length + 30);
        let cls = "";
        if (cmdlet) cls = "f";
        else if (L.kw.has(kwOf(w))) cls = "k";
        else if (L.lit.has(kwOf(w))) cls = "l";
        else if (L.keys && lineStart && /^\s*[:=]/.test(after)) cls = "p";
        else if (/^\s*\(/.test(after)) cls = "f";
        else if (!L.nocase && /^[A-Z][a-z0-9]\w*$/.test(w)) cls = "t";
        else if (code[i - 1] === ".") cls = "p";
        out += cls ? span(cls, w) : esc(w);
        i += w.length; lineStart = false; continue;
      }
      if (c === "\n") lineStart = true;
      else if (!/\s/.test(c)) lineStart = false;
      out += esc(c); i++;
    }
    return out;
  }

  function highlightCss(code) {
    let out = "";
    const re = /(\/\*[\s\S]*?(?:\*\/|$))|("(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?)|(@[\w-]+)|(#[\da-fA-F]{3,8}\b)|(-?\d*\.?\d+(?:px|em|rem|%|vh|vw|s|ms|deg|fr|ch|pt)?)|([\w-]+)(?=\s*:(?![^{]*\{))|([.#]?[\w-]+)(?=[^{};]*\{)|([\s\S])/g;
    let m;
    while ((m = re.exec(code))) {
      if (m[1]) out += span("c", m[1]);
      else if (m[2]) out += span("s", m[2]);
      else if (m[3]) out += span("k", m[3]);
      else if (m[4]) out += span("n", m[4]);
      else if (m[5]) out += span("n", m[5]);
      else if (m[6]) out += span("p", m[6]);
      else if (m[7]) out += span("t", m[7]);
      else out += esc(m[8]);
    }
    return out;
  }

  function highlightMarkup(code) {
    let out = "";
    const re = /(<!--[\s\S]*?(?:-->|$))|(<!\[CDATA\[[\s\S]*?(?:\]\]>|$))|(<\/?)([\w:.-]+)((?:[^>"']|"[^"]*"|'[^']*')*?)(\/?>)|(&[#\w]+;)|([\s\S])/g;
    let m;
    while ((m = re.exec(code))) {
      if (m[1]) out += span("c", m[1]);
      else if (m[2]) out += span("s", m[2]);
      else if (m[3]) {
        out += esc(m[3]) + span("k", m[4]);
        out += m[5].replace(/([\w:.@-]+)(\s*=\s*)?("[^"]*"|'[^']*'|[^\s"'>]+)?|([\s\S])/g, (all, name, eq, val, other) =>
          other !== undefined ? esc(other) : span("p", name) + (eq ? esc(eq) : "") + (val ? span("s", val) : ""));
        out += esc(m[6]);
      } else if (m[7]) out += span("n", m[7]);
      else out += esc(m[8]);
    }
    return out;
  }

  function highlightDiff(code) {
    return code.split("\n").map((line) => {
      if (/^(\+\+\+|---)\s/.test(line) || /^(diff|index) /.test(line)) return span("m", line);
      if (line.startsWith("@@")) return span("f", line);
      if (line.startsWith("+")) return span("ins", line);
      if (line.startsWith("-")) return span("del", line);
      return esc(line);
    }).join("\n");
  }

  /** `code` as HTML with colours, and the language it was taken to be ("" for none). */
  function highlight(code, name) {
    const lang = langOf(name, code);
    const L = LANGS[lang];
    if (!L) return { html: esc(code), lang: "" };
    if (code.length > 200000) return { html: esc(code), lang };        // (a huge paste: shown at once, plain)
    const html = L.css ? highlightCss(code) : L.markup ? highlightMarkup(code) : L.diff ? highlightDiff(code) : highlightGeneric(code, L);
    return { html, lang };
  }

  // ---------------------------------------------------------------- inline
  const OK_URL = /^(https?:\/\/|mailto:)/i;

  function inline(text) {
    const held = [];
    const hold = (html) => `\u0001${held.push(html) - 1}\u0002`;
    let s = String(text).replace(/[\u0001\u0002]/g, "");
    s = s.replace(/(`+)([\s\S]*?[^`])\1(?!`)/g, (_, ticks, code) => hold(`<code>${esc(code.replace(/^ (.*) $/, "$1"))}</code>`));
    s = s.replace(/\[([^\]\n]+)\]\(\s*<?([^\s)>]+)>?(?:\s+"[^"]*")?\s*\)/g, (all, label, url) =>
      OK_URL.test(url) ? hold(`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${emph(esc(label))}</a>`) : all);
    s = s.replace(/<?\b((?:https?:\/\/|www\.)[^\s<>]*[^\s<>.,;:!?)\]'"])>?/g, (all, url) => {
      const href = /^www\./i.test(url) ? "https://" + url : url;
      return hold(`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`);
    });
    s = emph(esc(s));
    return s.replace(/\u0001(\d+)\u0002/g, (_, k) => held[+k]);
  }

  function emph(s) {
    return s
      .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?![\w])/g, "$1<strong>$2</strong>")
      .replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>")
      .replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])/g, "$1<em>$2</em>")
      .replace(/(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?![\w])/g, "$1<em>$2</em>")
      .replace(/==(?=\S)([^=\n]*?\S)==/g, "<mark>$1</mark>");
  }

  // ---------------------------------------------------------------- blocks
  const FENCE = /^(\s*)(`{3,}|~{3,})\s*([^\s`]*)[^`]*$/;
  const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
  const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
  const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  const QUOTE = /^\s{0,3}>\s?(.*)$/;
  const TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

  function cells(line) {
    let t = line.trim();
    if (t.startsWith("|")) t = t.slice(1);
    if (t.endsWith("|") && !t.endsWith("\\|")) t = t.slice(0, -1);
    return t.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
  }

  let codeCount = 0;

  /** Lines `lines` (from line number `first` of the note) as HTML. */
  function blocks(lines, first) {
    let out = "", i = 0;
    const startsBlock = (l, next) => FENCE.test(l) || HEADING.test(l) || RULE.test(l) || ITEM.test(l) || QUOTE.test(l)
      || (l.includes("|") && next !== undefined && TABLE_SEP.test(next) && next.includes("-"));
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      let m;
      if ((m = FENCE.exec(line))) {
        const indent = m[1].length, mark = m[2], lang = m[3];
        const body = [];
        i++;
        while (i < lines.length && !(lines[i].trim().startsWith(mark[0].repeat(mark.length)) && /^\s*[`~]+\s*$/.test(lines[i]))) {
          body.push(lines[i].slice(Math.min(indent, lines[i].length - lines[i].trimStart().length)));
          i++;
        }
        i++;
        const code = body.join("\n");
        const hl = highlight(code, lang);
        const label = NAMES[hl.lang] || (lang ? lang : "Code");
        out += `<div class="md-code"><div class="md-code-bar"><span>${esc(label)}</span>` +
               `<button class="md-copy" type="button" data-code="${codeCount++}">Copy</button></div>` +
               `<pre><code>${hl.html}\n</code></pre></div>`;
        continue;
      }
      if ((m = HEADING.exec(line))) {
        out += `<h${m[1].length} class="md-h">${inline(m[2])}</h${m[1].length}>`;
        i++; continue;
      }
      if (RULE.test(line)) { out += "<hr>"; i++; continue; }
      if (QUOTE.test(line)) {
        const inner = [];
        const start = i;
        while (i < lines.length && (m = QUOTE.exec(lines[i]))) { inner.push(m[1]); i++; }
        out += `<blockquote>${blocks(inner, first + start)}</blockquote>`;
        continue;
      }
      if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
        const head = cells(line);
        const align = cells(lines[i + 1]).map((c) => (/^:-+:$/.test(c) ? "c" : /-+:$/.test(c) ? "r" : ""));
        const cls = (k) => (align[k] ? ` class="md-${align[k]}"` : "");
        let t = "<div class=\"md-table\"><table><thead><tr>" + head.map((c, k) => `<th${cls(k)}>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
        i += 2;
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          const row = cells(lines[i]);
          t += "<tr>" + head.map((_, k) => `<td${cls(k)}>${inline(row[k] || "")}</td>`).join("") + "</tr>";
          i++;
        }
        out += t + "</tbody></table></div>";
        continue;
      }
      if (ITEM.test(line)) {
        const items = [];
        while (i < lines.length) {
          const l = lines[i];
          if ((m = ITEM.exec(l))) {
            const indent = m[1].replace(/\t/g, "    ").length;
            if (items.length && indent <= items[0].indent && /\d/.test(m[2]) !== items[0].ordered) break;   // (another kind of list)
            items.push({ indent: m[1].replace(/\t/g, "    ").length, ordered: /\d/.test(m[2]), num: parseInt(m[2], 10),
                         text: [m[3]], line: first + i });
          } else if (l.trim() && items.length && (/^\s{2,}/.test(l) || !startsBlock(l, lines[i + 1])) && lines[i - 1].trim()) {
            items[items.length - 1].text.push(l.trim());              // (the item goes on on the next line)
          } else break;
          i++;
        }
        out += list(items, 0, items.length);
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !(para.length && startsBlock(lines[i], lines[i + 1]))) {
        para.push(lines[i]); i++;
      }
      out += `<p>${para.map(inline).join("<br>")}</p>`;
    }
    return out;
  }

  function list(items, from, to) {
    let out = "", i = from;
    const base = items[from].indent;
    const ordered = items[from].ordered;
    const startAt = ordered && items[from].num !== 1 ? ` start="${items[from].num}"` : "";
    while (i < to) {
      const it = items[i];
      let j = i + 1;
      while (j < to && items[j].indent > base) j++;            // (its own list, further in)
      let text = it.text.join("\n");
      let box = "";
      const task = /^\[([ xX])\]\s+/.exec(text);
      if (task) {
        box = `<input type="checkbox" class="md-task" data-line="${it.line}"${task[1] !== " " ? " checked" : ""}>`;
        text = text.slice(task[0].length);
      }
      out += `<li${task ? ' class="md-task-item' + (task[1] !== " " ? " done" : "") + '"' : ""}>${box}<span>${text.split("\n").map(inline).join("<br>")}</span>`;
      if (j > i + 1) out += list(items, i + 1, j);
      out += "</li>";
      i = j;
    }
    return ordered ? `<ol${startAt}>${out}</ol>` : `<ul>${out}</ul>`;
  }

  /** A note's text as HTML (Preview). */
  function render(text) {
    codeCount = 0;
    return blocks(String(text || "").replace(/\r\n?/g, "\n").split("\n"), 0);
  }

  /** Line `n` of a note's text with its checkbox ticked or not (a task item clicked in Preview). */
  function toggleTask(text, n) {
    const lines = String(text).split("\n");
    if (n < 0 || n >= lines.length) return text;
    lines[n] = lines[n].replace(/^(\s*(?:[-*+]|\d{1,9}[.)])\s+)\[([ xX])\]/, (_, pre, mark) => pre + (mark === " " ? "[x]" : "[ ]"));
    return lines.join("\n");
  }

  window.ddMarkdown = { render, highlight, toggleTask, esc, inline };
})();
