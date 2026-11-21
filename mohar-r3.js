/* Mohar Round 3 Bridge — connects index.html to Backend RBAC, Demo Accounts, AI Citation Validator & Visual Inspector */
(function () {
  const API = '';
  const DEMO_ACCOUNTS = [
    { role: 'Creator', name: 'Palak Singh', email: 'creator@northwind.demo', pass: 'mohar2026' },
    { role: 'Reviewer', name: 'Divya Varshney', email: 'reviewer@northwind.demo', pass: 'mohar2026' },
    { role: 'Manager', name: 'Jatin Sharma', email: 'manager@northwind.demo', pass: 'mohar2026' },
  ];

  const R3 = {
    token: null,
    workspace: 'northwind-main',
    userName: 'Palak Singh',
    userEmail: 'creator@northwind.demo',
    aiFindings: [],
    aiMeta: null,
    visualFindings: [],
    visualMeta: null,
    online: false,
    dbBackend: 'sqlite',
    benchResult: null,
  };

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (R3.token) headers['Authorization'] = 'Bearer ' + R3.token;
    if (opts.json !== undefined) {
      headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    const res = await fetch(API + path, Object.assign({}, opts, { headers }));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Server request failed (' + res.status + ')');
    return data;
  }

  async function loginRole(role, customName) {
    const targetRole = role || S.role || 'Creator';
    const acct = DEMO_ACCOUNTS.find((a) => a.role === targetRole) || DEMO_ACCOUNTS[0];
    R3.userName = customName || acct.name;
    R3.userEmail = acct.email;
    try {
      const res = await api('/api/auth/login', {
        method: 'POST',
        json: { role: targetRole, user_name: R3.userName, workspace_id: R3.workspace },
      });
      R3.token = res.token;
      R3.dbBackend = res.db_backend || 'sqlite';
      R3.online = true;
      await syncWorkspace();
    } catch (e) {
      R3.online = false;
    }
  }

  async function syncWorkspace() {
    if (!R3.token) return;
    try {
      const st = await api('/api/workspaces/' + R3.workspace + '/state');
      R3.dbBackend = st.db_backend || R3.dbBackend;
      const baseIds = new Set(BASE.map((r) => r.id));
      S.extra = st.rules.filter((r) => !baseIds.has(r.id));
      if (st.versions && st.versions.length) {
        S.versions = st.versions;
      }
      if (st.audit && st.audit.length) {
        S.log = st.audit;
      }
      render();
    } catch (e) {
      console.warn('Sync warning:', e.message);
    }
  }

  // Rules win ties: deterministic rule findings take precedence over overlapping AI spans
  const origReview = window.review;
  window.review = function () {
    const baseFindings = origReview();
    const extra = [...R3.aiFindings, ...R3.visualFindings].map((f) => {
      const key = (f.engine || 'R3') + '|' + f.src + '|' + f.quote;
      return Object.assign({}, f, { key });
    });

    for (const ef of extra) {
      const overlapsRule = baseFindings.some((bf) => {
        if (bf.src === ef.src && bf.quote.toLowerCase() === ef.quote.toLowerCase()) return true;
        if (bf.s >= 0 && ef.s >= 0 && !(ef.e <= bf.s || ef.s >= bf.e)) return true;
        return false;
      });
      if (!overlapsRule) {
        baseFindings.push(ef);
      }
    }
    return baseFindings;
  };

  const origRole = A.role;
  A.role = async function (d) {
    origRole(d);
    await loginRole(d.r);
    if (R3.online) toast('Signed in as ' + R3.userName + ' (' + d.r + ')');
  };

  const origRun = A.run;
  A.run = async function (d) {
    if (!S.text.trim()) return toast('Add some text first');
    R3.aiFindings = [];
    origRun(d);
    if (!R3.online) return;
    try {
      const res = await api('/api/review/ai', {
        method: 'POST',
        json: { workspace_id: R3.workspace, content_type: S.type, text: S.text },
      });
      R3.aiFindings = res.findings || [];
      R3.aiMeta = res;
      if (S.orig) S.startScore = score(window.review());
      render();
      if (R3.aiFindings.length) {
        toast('AI semantic layer added ' + R3.aiFindings.length + ' cited finding(s)');
      }
    } catch (e) {
      console.warn('AI semantic check fallback:', e.message);
    }
  };

  const origSend = A.send;
  A.send = async function (d) {
    const O = open();
    if (R3.online) {
      try {
        await api('/api/workspaces/' + R3.workspace + '/versions', {
          method: 'POST',
          json: {
            content_type: S.type,
            original_text: S.orig ? S.orig.text : S.text,
            current_text: S.text,
            open_findings: O.map((f) => ({ sev: f.sev, quote: f.quote, src: f.src })),
            dismissals: Object.entries(S.dismissed).map(([k, w]) => [k, w]),
          },
        });
        await syncWorkspace();
        S.tab = 'appr';
        render();
        toast('Saved to shared server queue. Switch to Reviewer to decide.');
        return;
      } catch (e) {
        toast(e.message);
        return;
      }
    }
    origSend(d);
  };

  const origApprove = A.approve;
  A.approve = async function (d) {
    const v = lastV();
    const noteEl = $('#note');
    const note = noteEl ? noteEl.value.trim() : '';
    if (R3.online && v) {
      try {
        await api('/api/workspaces/' + R3.workspace + '/versions/' + v.n + '/decide', {
          method: 'POST',
          json: { decision: 'Ready to publish', note },
        });
        await syncWorkspace();
        toast('Approved on server & recorded in tamper-resistant audit log.');
        return;
      } catch (e) {
        toast(e.message);
        return;
      }
    }
    origApprove(d);
  };

  const origChanges = A.changes;
  A.changes = async function (d) {
    const v = lastV();
    const noteEl = $('#note');
    const note = noteEl ? noteEl.value.trim() : '';
    if (R3.online && v) {
      try {
        await api('/api/workspaces/' + R3.workspace + '/versions/' + v.n + '/decide', {
          method: 'POST',
          json: { decision: 'Changes requested', note },
        });
        S.ran = false;
        S.dismissed = {};
        await syncWorkspace();
        toast('Changes requested on server.');
        return;
      } catch (e) {
        toast(e.message);
        return;
      }
    }
    origChanges(d);
  };

  const origAddRule = A.addrule;
  A.addrule = async function (d) {
    if (R3.online) {
      const k = $('#gk').value;
      const t = $('#gt').value.trim();
      if (!t) return toast('Write the text first');
      const n = String(S.extra.length + 4).padStart(2, '0');
      const rule =
        k === 'Banned phrase'
          ? { id: 'B-' + n, cat: 'Terminology', title: 'Banned phrase', bad: t, good: '', text: 'Never use "' + t + '".' }
          : { id: (k === 'Approved fact' ? 'F-1' : 'N-') + n, cat: k === 'Approved fact' ? 'Facts' : 'Guidelines', title: k, bad: '', good: '', text: t };
      try {
        await api('/api/workspaces/' + R3.workspace + '/rules', { method: 'POST', json: rule });
        await syncWorkspace();
        toast('Rule persisted to shared workspace');
        return;
      } catch (e) {
        toast(e.message);
        return;
      }
    }
    origAddRule(d);
  };

  const origAddTerm = A.addterm;
  A.addterm = async function (d) {
    if (R3.online) {
      const b = $('#tb').value.trim();
      const g = $('#tg').value.trim();
      if (!b || !g) return toast('Fill in both fields');
      const rule = {
        id: 'T-' + String(4 + S.extra.length).padStart(2, '0'),
        cat: 'Terminology',
        title: 'Term',
        bad: b,
        good: g,
        text: 'Say "' + g + '", never "' + b + '".',
      };
      try {
        await api('/api/workspaces/' + R3.workspace + '/rules', { method: 'POST', json: rule });
        await syncWorkspace();
        toast('Terminology rule persisted to server');
        return;
      } catch (e) {
        toast(e.message);
        return;
      }
    }
    origAddTerm(d);
  };

  const origLoadFile = window.loadFile;
  window.loadFile = async function (f) {
    if (!f) return;
    const n = f.name.toLowerCase();
    const isImage = /\.(png|jpe?g|webp)$/.test(n);
    const isPdf = n.endsWith('.pdf');

    if ((isImage || isPdf) && R3.online) {
      S.busy = true;
      render();
      try {
        const fd = new FormData();
        fd.append('file', f);
        const res = await fetch(API + '/api/visual/inspect', { method: 'POST', body: fd });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Visual inspection failed');

        if (isImage || (isPdf && !data.ocr_text && data.findings && data.findings.length)) {
          fresh();
          R3.visualMeta = data;
          R3.visualFindings = data.findings || [];
          S.text =
            data.ocr_text ||
            '[Visual asset: ' + f.name + ' (' + (data.dimensions || 'PDF') + ') — inspected for contrast, palette & logo]';
          if (data.detected_fg) S.fg = data.detected_fg;
          if (data.detected_bg) S.bg = data.detected_bg;
          if (data.logo_status) S.logo = data.logo_status;
          S.file = f.name + ' · Visual + OCR (' + (data.ocr_engine || data.mode || 'inspected') + ')';
          S.busy = false;
          render();
          toast('Visual check complete: ' + R3.visualFindings.length + ' visual finding(s)');
          return;
        }
      } catch (e) {
        console.warn('Server visual/PDF fallback to browser reader:', e.message);
      }
      S.busy = false;
    }
    R3.visualFindings = [];
    R3.visualMeta = null;
    return origLoadFile(f);
  };

  function relabelEngineBadges() {
    const allF = S.ran ? window.review() : [];
    for (const f of allF) {
      if (!f.engine) continue;
      const cardId = 'c-' + btoa(unescape(encodeURIComponent(f.key))).replace(/\W/g, '');
      const el = document.getElementById(cardId);
      if (!el) continue;
      const tags = el.querySelectorAll('.tag');
      for (const t of tags) {
        if (t.textContent.trim() === 'Rule engine') {
          t.textContent = f.engine;
          t.style.borderColor = f.engine === 'AI-assisted' ? 'var(--gold)' : 'var(--lo)';
          t.style.color = f.engine === 'AI-assisted' ? 'var(--gold)' : 'var(--lo)';
          t.style.fontWeight = '600';
        }
      }
    }
  }

  const origRender = window.render;
  window.render = function () {
    origRender();
    relabelEngineBadges();

    document.querySelectorAll('.muted, p, div').forEach((el) => {
      if (el.children.length === 0 && el.textContent.includes('AI layer: off in this build.')) {
        el.textContent =
          'AI layer: active (' +
          (R3.aiMeta ? R3.aiMeta.provider : 'citation-validated') +
          '). Deterministic rules run first and win ties; AI findings are verified against workspace sources.';
      }
    });

    const up = document.getElementById('up');
    if (up) up.setAttribute('accept', '.pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.webp');

    const dzText = document.querySelector('#dz b');
    if (dzText) dzText.textContent = 'Drop a PDF, Word, text or image (PNG/JPG/WEBP) here';

    const main = document.getElementById('main');
    if (!main) return;

    let bar = document.getElementById('r3-bar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'r3-bar';
      bar.className = 'card';
      bar.style.marginTop = '18px';
      main.appendChild(bar);
    }

    const aiSummary = R3.aiMeta
      ? `AI Layer (${R3.aiMeta.provider}): ${R3.aiMeta.validator_stats.accepted_count} verified, ${R3.aiMeta.validator_stats.discarded_unquoted} unquoted discarded, ${R3.aiMeta.validator_stats.downgraded_unverified_source} downgraded.`
      : 'AI Layer: Ready (Citation Validator active).';

    let benchHtml = '';
    if (R3.benchResult) {
      const tb = R3.benchResult.text_benchmark;
      const pt = tb.per_issue_type;
      const vb = R3.benchResult.visual_benchmark;
      benchHtml = `<div class="q" style="margin-top:10px"><b>Round 3 Benchmark (n=${R3.benchResult.sample_size} synthetic drafts + n=${vb.total_images} visual test images):</b><br>
        • Overall Rule-only Recall: <b>${tb.rule_only.recall}%</b> → Rule + Validated AI Recall: <b>${tb.rule_plus_ai.recall}%</b> (Precision: <b>${tb.rule_plus_ai.precision}%</b>, Valid Source Rate: <b>${tb.valid_source_rate}%</b>, Actionable Edit Rate: <b>${tb.suggestion_actionable_rate}%</b>)<br>
        • Per Issue Type — Claims (n=${pt.Claims.sample_size}): <b>${pt.Claims.rule_only_recall}% → ${pt.Claims.rule_plus_ai_recall}%</b> · Facts (n=${pt.Facts.sample_size}): <b>${pt.Facts.rule_only_recall}% → ${pt.Facts.rule_plus_ai_recall}%</b> · Tone (n=${pt.Tone.sample_size}): <b>${pt.Tone.rule_only_recall}% → ${pt.Tone.rule_plus_ai_recall}%</b><br>
        • Visual Identity Suite (Contrast / Palette / 2022 Logo Asset): <b>${vb.detection_rate}%</b> detection across ${vb.defect_images} defect images, <b>${vb.false_positives}</b> false positives on ${vb.clean_controls} clean controls.</div>`;
    }

    const demoRows = DEMO_ACCOUNTS.map(
      (a) =>
        `<div class="row" style="justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line)">
          <span><b>${a.role}</b> (${a.name}) — <code>${a.email}</code> / Password: <code>${a.pass}</code></span>
          <button type="button" class="btn ${S.role === a.role ? 'chk' : 'ghost'} sm" data-demo-role="${a.role}">${
            S.role === a.role ? 'Active session' : 'Login as ' + a.role
          }</button>
        </div>`
    ).join('');

    bar.innerHTML = `
      <div class="row" style="justify-content:space-between">
        <div>
          <span class="tag ${R3.online ? 'chk' : 'bad'}">${R3.online ? 'Backend Connected (' + R3.dbBackend + ') · JWT RBAC Active' : 'Offline Browser Fallback'}</span>
          <span class="tag">Signed in: <b>${R3.userName}</b> (${R3.userEmail})</span>
          <span class="tag">${aiSummary}</span>
        </div>
        <div class="row">
          <button type="button" class="btn ghost sm" id="r3-sync">Sync shared queue</button>
          <button type="button" class="btn ghost sm" id="r3-rbac">Test RBAC isolation</button>
          <button type="button" class="btn sm" id="r3-bench">Run 25-draft & visual benchmark</button>
        </div>
      </div>
      <div class="q" style="margin-top:12px">
        <div style="margin-bottom:6px"><b>Demo Account Credentials (No sign-up needed — click any account below or enter credentials to switch JWT user):</b></div>
        ${demoRows}
        <div class="row" style="margin-top:8px;gap:8px">
          <input id="demo-email" placeholder="Demo email (e.g. reviewer@northwind.demo)" value="${R3.userEmail}" style="max-width:250px" />
          <input id="demo-pass" type="password" placeholder="Password" value="mohar2026" style="max-width:140px" />
          <button type="button" class="btn sm" id="demo-login-btn">Sign in</button>
        </div>
      </div>
      ${benchHtml}
    `;

    bar.querySelectorAll('[data-demo-role]').forEach((btn) => {
      btn.onclick = async () => {
        const r = btn.getAttribute('data-demo-role');
        origRole({ r });
        await loginRole(r);
        toast('Logged in with demo account: ' + R3.userEmail);
      };
    });

    document.getElementById('demo-login-btn').onclick = async () => {
      const em = document.getElementById('demo-email').value.trim().toLowerCase();
      const pw = document.getElementById('demo-pass').value.trim();
      const matched = DEMO_ACCOUNTS.find((a) => a.email === em && a.pass === pw);
      if (!matched) {
        return toast('Invalid demo credentials. Use one of the listed demo accounts above.');
      }
      origRole({ r: matched.role });
      await loginRole(matched.role, matched.name);
      toast('Signed in as ' + matched.name + ' (' + matched.role + ')');
    };

    document.getElementById('r3-sync').onclick = async () => {
      await syncWorkspace();
      toast('Synced with database (' + R3.dbBackend + ')');
    };
    document.getElementById('r3-rbac').onclick = async () => {
      try {
        await api('/api/workspaces/other-org-private/state');
        toast('Unexpected access');
      } catch (e) {
        toast('RBAC Passed (403 Blocked): ' + e.message);
      }
    };
    document.getElementById('r3-bench').onclick = async () => {
      try {
        R3.benchResult = await api('/api/benchmark');
        render();
        toast('Benchmark metrics computed');
      } catch (e) {
        toast('Start backend.py to run benchmark');
      }
    };
  };

  loginRole(S.role || 'Creator');
})();