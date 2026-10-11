"use strict";
/* The page of a file request: sends the chosen files, one after the other, with progress. */
(function () {
  var drop = document.getElementById("drop"), list = document.getElementById("list"), pick = document.getElementById("pick");
  var id = drop.dataset.id, max = Number(drop.dataset.max), queue = [], busy = false;
  function size(n) { var u = ["B", "KB", "MB", "GB"], i = 0; while (n >= 1024 && i < 3) { n /= 1024; i++; } return (i ? n.toFixed(1) : n) + " " + u[i]; }
  function add(files) {
    Array.prototype.forEach.call(files, function (f) {
      var row = document.createElement("div"); row.className = "up";
      var n = document.createElement("span"); n.className = "n"; n.textContent = f.name;
      var s = document.createElement("span"); s.className = "s"; s.textContent = size(f.size);
      row.appendChild(n); row.appendChild(s); list.appendChild(row);
      if (f.size > max) { row.className = "up bad"; s.textContent = "Too big (limit " + size(max) + ")"; return; }
      queue.push({ f: f, row: row, s: s });
    });
    next();
  }
  function next() {
    if (busy || !queue.length) return;
    busy = true;
    var job = queue.shift(), bar = document.createElement("progress");
    bar.max = 100; bar.value = 0; job.row.insertBefore(bar, job.s);
    var x = new XMLHttpRequest();
    x.open("POST", "/s/" + encodeURIComponent(id) + "/upload?name=" + encodeURIComponent(job.f.name));
    x.upload.onprogress = function (e) { if (e.lengthComputable) { bar.value = (e.loaded / e.total) * 100; job.s.textContent = Math.round(bar.value) + "%"; } };
    x.onloadend = function () {
      bar.remove();
      var ok = x.status === 200, msg = "Could not be sent";
      try { var r = JSON.parse(x.responseText); if (ok) msg = "Sent"; else if (r.error) msg = r.error; } catch (e) { /* not JSON */ }
      job.row.className = ok ? "up" : "up bad"; job.s.textContent = msg + (ok ? " \u2713" : "");
      busy = false; next();
    };
    x.send(job.f);
  }
  pick.addEventListener("change", function () { add(pick.files); pick.value = ""; });
  ["dragenter", "dragover"].forEach(function (t) { document.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("on"); }); });
  ["dragleave", "drop"].forEach(function (t) { document.addEventListener(t, function (e) { e.preventDefault(); drop.classList.remove("on"); }); });
  document.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files) add(e.dataTransfer.files); });
})();
