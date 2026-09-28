"use strict";
(() => {
  const nonPagePrefixes = ["/login", "/auth", "/api", "/assets", "/lib", "/oauth", "/workspace/assets", "/笔记库"];

  function safeReturnPath(raw) {
    if (typeof raw !== "string" || !raw || [...raw].length > 512 || /%(?![0-9a-fA-F]{2})/.test(raw)) return null;
    try {
      encodeURIComponent(raw); // Reject unpaired surrogates before URL serialization.
      let value = raw;
      for (let layer = 0; layer < 8; layer += 1) {
        if (!value.startsWith("/") || value.startsWith("//") || /[\\\u0000-\u001f\u007f]/.test(value)) return null;
        const beforeHash = value.split("#", 1)[0];
        const queryAt = beforeHash.indexOf("?");
        const pathname = queryAt === -1 ? beforeHash : beforeHash.slice(0, queryAt);
        const query = queryAt === -1 ? "" : beforeHash.slice(queryAt + 1);
        const segments = pathname.split("/");
        if (segments.some(segment => segment === "." || segment === "..")) return null;
        const canonical = "/" + segments.filter(Boolean).map(segment => segment.split(";", 1)[0]).join("/").toLowerCase();
        if (nonPagePrefixes.some(prefix => canonical === prefix || canonical.startsWith(prefix + "/")) || new URLSearchParams(query).has("returnTo")) return null;
        const decoded = value.replace(/(?:%[0-9a-fA-F]{2})+/g, part => decodeURIComponent(part));
        if (decoded === value) return raw;
        value = decoded;
      }
    } catch { /* Invalid UTF-8 and malformed URLs must not reach navigation. */ }
    return null;
  }

  function loginUrl(target, fallback = "/workspace/", result = "") {
    const params = new URLSearchParams({returnTo: safeReturnPath(target) || safeReturnPath(fallback) || "/workspace/"});
    if (result) params.set("result", result);
    return "/login?" + params.toString();
  }

  function currentTarget(fallback = "/workspace/") {
    return safeReturnPath(location.pathname + location.search + location.hash) || fallback;
  }

  window.LoginRedirect = Object.freeze({safeReturnPath, loginUrl, currentTarget});
})();
