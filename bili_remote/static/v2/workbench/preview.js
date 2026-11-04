/* Display formatting only: no title, subtitle, naming or publishing generation. */
(function (global) {
  "use strict";
  const allowedURL = value => { try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; } };
  function appendInline(host, text, format) {
    const expression = format === "bbcode" ? /\[img\]([\s\S]*?)\[\/img\]|\[url=([^\]]+)\]([\s\S]*?)\[\/url\]|\[(b|i|u)\]([\s\S]*?)\[\/\4\]/gi : /!\[([^\]]*)\]\(([^)]+)\)|\[([^\]]+)\]\(([^)]+)\)|\*\*(.+?)\*\*|`([^`]+)`/g;
    let cursor = 0;
    for (const match of text.matchAll(expression)) {
      host.append(document.createTextNode(text.slice(cursor, match.index)));
      let element;
      if (format === "bbcode") {
        if (match[1] && allowedURL(match[1])) { element = document.createElement("img"); element.src = allowedURL(match[1]); element.alt = "发布图片"; }
        else if (match[2] && allowedURL(match[2])) { element = document.createElement("a"); element.href = allowedURL(match[2]); element.textContent = match[3]; }
        else if (match[4]) { element = document.createElement({b: "strong", i: "em", u: "u"}[match[4].toLowerCase()]); element.textContent = match[5]; }
      } else {
        if (match[2] && allowedURL(match[2])) { element = document.createElement("img"); element.src = allowedURL(match[2]); element.alt = match[1] || "发布图片"; }
        else if (match[4] && allowedURL(match[4])) { element = document.createElement("a"); element.href = allowedURL(match[4]); element.textContent = match[3]; }
        else if (match[5] || match[6]) { element = document.createElement(match[5] ? "strong" : "code"); element.textContent = match[5] || match[6]; }
      }
      if (element?.tagName === "A") { element.target = "_blank"; element.rel = "noopener noreferrer"; }
      if (element?.tagName === "IMG") { element.loading = "lazy"; element.referrerPolicy = "no-referrer"; }
      host.append(element || document.createTextNode(match[0])); cursor = match.index + match[0].length;
    }
    host.append(document.createTextNode(text.slice(cursor)));
  }
  function render(host, text, format) {
    host.replaceChildren();
    let code = null;
    for (const line of String(text).split("\n")) {
      const boundary = format === "bbcode" ? /^\[\/?(?:code|quote)\]$/i.test(line.trim()) : /^```/.test(line);
      if (boundary) { if (code) code = null; else { code = document.createElement("pre"); host.append(code); } continue; }
      if (code) { code.append(document.createTextNode(line + "\n")); continue; }
      const heading = format === "markdown" ? /^(#{1,6})\s+(.*)/.exec(line) : null;
      const row = document.createElement(heading ? `h${heading[1].length}` : "p"); appendInline(row, heading ? heading[2] : line, format); host.append(row);
    }
  }
  global.BiliWorkbenchPreview = {render};
})(window);
