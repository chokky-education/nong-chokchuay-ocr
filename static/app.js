// น้องโชคช่วย (Nong ChokChuay) — Teacher Desk & LINE OA Simulator Frontend

(() => {
  'use strict';

  const PRESET_TO_FILE = {
    classroom_receipt: 'sample_kindergarten_receipt.png',
    student_profile: 'sample_student_profile.png',
    school_circular: 'sample_school_notice.png',
  };

  const PRESET_LABELS = {
    classroom_receipt: '🧾 ส่งรูปบิลค่าสีเทียน/สื่อการสอน',
    student_profile: '👶 ส่งใบประวัติเด็กอนุบาล/แพ้อาหาร',
    school_circular: '🏫 ส่งหนังสือแจ้งผู้ปกครอง',
  };

  const CATEGORY_LABELS = {
    general: '📁 เอกสารทั่วไป',
    receipt: '🧾 ใบเสร็จ / สลิปเบิกจ่าย',
    student: '👶 ทะเบียนเด็ก / แพ้อาหาร',
    school: '🏫 หนังสือโรงเรียน / ผู้ปกครอง',
    teaching: '🎨 แผนการสอน / สื่ออนุบาล',
    official: '📝 บันทึกข้อความราชการ',
    tax: '🏛️ ภาษี / บัญชี',
    contract: '📜 สัญญา / ระเบียบ',
    manual: '📘 คู่มือ / อ้างอิง',
  };

  const EXPENSE_CAT_LABELS = {
    classroom: '🎨 สื่อการสอน / ห้องเรียน',
    kids_snack: '🥛 นม / ขนมเด็ก',
    food: '🍜 อาหาร / กาแฟ',
    transport: '🚕 เดินทาง / น้ำมัน',
    utilities: '💡 ค่าน้ำ / ค่าไฟ / เน็ต',
    office: '📎 อุปกรณ์สำนักงาน',
    other: '📦 อื่นๆ',
  };

  const REIMB_LABELS = {
    pending_reimbursement: '💸 รอเบิกคืนโรงเรียน',
    reimbursed: '✅ เบิกคืนแล้ว',
    personal: '👤 จ่ายส่วนตัว',
  };

  const state = {
    currentDocId: null,
    currentDocData: null,
    currentFile: null,
    currentIsPdf: false,
    currentPdfPage: 1,
    currentPdfTotalPages: 1,
    currentLines: [],
    lineBoxes: [],
  };

  const $ = (id) => document.getElementById(id);

  const els = {
    // Header & Hero
    btnOpenLineSim: $('btn-open-line-sim'),
    btnOpenExpenses: $('btn-open-expenses'),
    pendingReimbBadge: $('pending-reimb-badge'),
    btnOpenStudents: $('btn-open-students'),
    studentCountBadge: $('student-count-badge'),
    btnOpenDb: $('btn-open-db'),
    dbCountBadge: $('db-count-badge'),
    btnScanInbox: $('btn-scan-inbox'),
    inboxCountBadge: $('inbox-count-badge'),
    nongChokSpeech: $('nong-chok-speech'),
    kpiCardPendingReview: $('kpi-card-pending-review'),
    kpiPendingReviewVal: $('kpi-pending-review-val'),
    kpiCardPendingReimb: $('kpi-card-pending-reimb'),
    kpiPendingReimbVal: $('kpi-pending-reimb-val'),
    kpiCardStudents: $('kpi-card-students'),
    kpiStudentsVal: $('kpi-students-val'),
    kpiAllergyNote: $('kpi-allergy-note'),
    kpiCardVault: $('kpi-card-vault'),
    kpiVaultVal: $('kpi-vault-val'),

    // Left Viewer
    btnSelectFile: $('btn-select-file'),
    fileInput: $('file-input'),
    fileBadge: $('file-badge'),
    dropzone: $('dropzone'),
    pdfControls: $('pdf-controls'),
    btnPrevPage: $('btn-prev-page'),
    btnNextPage: $('btn-next-page'),
    btnOcrAllPages: $('btn-ocr-all-pages'),
    pdfPageIndicator: $('pdf-page-indicator'),
    canvasWrapper: $('canvas-wrapper'),
    docImage: $('doc-image'),
    boxesLayer: $('boxes-layer'),
    viewerPlaceholder: $('viewer-placeholder'),
    loadingOverlay: $('loading-overlay'),
    loadingText: $('loading-text'),
    toggleAutoExtract: $('toggle-auto-extract'),
    toggleTechMode: $('toggle-tech-mode'),
    toggleBoxes: $('toggle-boxes'),
    docDimensions: $('doc-dimensions'),

    // Right Review Desk
    reviewStatusPill: $('review-status-pill'),
    reviewHelperText: $('review-helper-text'),
    btnConfirmReview: $('btn-confirm-review'),
    statTime: $('stat-time'),
    statLines: $('stat-lines'),
    statConf: $('stat-conf'),
    statDbStatus: $('stat-db-status'),
    docCategoryInput: $('doc-category-input'),
    docTagsInput: $('doc-tags-input'),
    docDateInput: $('doc-date-input'),
    btnSaveDoc: $('btn-save-doc'),
    studentBanner: $('student-banner'),
    studentBannerText: $('student-banner-text'),
    studentAllergyBadge: $('student-allergy-badge'),
    btnViewInStudents: $('btn-view-in-students'),
    expenseBanner: $('expense-banner'),
    expenseBannerText: $('expense-banner-text'),
    btnMarkReimbursedInline: $('btn-mark-reimbursed-inline'),
    btnViewInExpenses: $('btn-view-in-expenses'),
    ocrTextResult: $('ocr-text-result'),
    charCount: $('char-count'),
    llmStatusBadge: $('llm-status-badge'),
    linesTbody: $('lines-tbody'),
    ocrMarkdownResult: $('ocr-markdown-result'),
    ocrJsonResult: $('ocr-json-result'),
    footerDocId: $('footer-doc-id'),
    btnExtractExpense: $('btn-extract-expense'),
    btnDownloadSource: $('btn-download-source'),
    btnCopy: $('btn-copy'),
    btnDownloadTxt: $('btn-download-txt'),
    btnDownloadJson: $('btn-download-json'),

    // Vault Modal
    dbModal: $('db-modal'),
    btnCloseDb: $('btn-close-db'),
    dbSearchInput: $('db-search-input'),
    dbReviewFilter: $('db-review-filter'),
    dbCategoryFilter: $('db-category-filter'),
    dbTagFilter: $('db-tag-filter'),
    btnClearSearch: $('btn-clear-search'),
    dbSummaryStats: $('db-summary-stats'),
    docsList: $('docs-list'),

    // Expense Modal
    expenseModal: $('expense-modal'),
    btnExportExpensesCsv: $('btn-export-expenses-csv'),
    btnCloseExpenses: $('btn-close-expenses'),
    expenseMonthFilter: $('expense-month-filter'),
    expenseReimbFilter: $('expense-reimb-filter'),
    expenseCategoryFilter: $('expense-category-filter'),
    expenseTagFilter: $('expense-tag-filter'),
    btnClearExpenseFilters: $('btn-clear-expense-filters'),
    expKpiPendingReimb: $('exp-kpi-pending-reimb'),
    expKpiCount: $('exp-kpi-count'),
    expKpiSubtotal: $('exp-kpi-subtotal'),
    expKpiVat: $('exp-kpi-vat'),
    expKpiTotal: $('exp-kpi-total'),
    expensesTbody: $('expenses-tbody'),

    // Student Modal
    studentModal: $('student-modal'),
    btnExportStudentsCsv: $('btn-export-students-csv'),
    btnCloseStudents: $('btn-close-students'),
    stuKpiTotal: $('stu-kpi-total'),
    stuKpiAllergy: $('stu-kpi-allergy'),
    stuKpiRooms: $('stu-kpi-rooms'),
    studentSearchInput: $('student-search-input'),
    studentClassroomFilter: $('student-classroom-filter'),
    studentAllergyOnly: $('student-allergy-only'),
    btnToggleAddStudent: $('btn-toggle-add-student'),
    btnClearStudentFilters: $('btn-clear-student-filters'),
    studentForm: $('student-form'),
    stuEditId: $('stu-edit-id'),
    stuFullName: $('stu-full-name'),
    stuNickname: $('stu-nickname'),
    stuCitizenId: $('stu-citizen-id'),
    stuBirthDate: $('stu-birth-date'),
    stuClassroom: $('stu-classroom'),
    stuParentName: $('stu-parent-name'),
    stuParentPhone: $('stu-parent-phone'),
    stuAllergies: $('stu-allergies'),
    stuMedical: $('stu-medical'),
    btnCancelStudentForm: $('btn-cancel-student-form'),
    studentsList: $('students-list'),

    // LINE Simulator Modal
    lineSimModal: $('line-sim-modal'),
    btnCloseLineSim: $('btn-close-line-sim'),
    lineChatMessages: $('line-chat-messages'),
    btnLineSimPhoto: $('btn-line-sim-photo'),
    lineSimFileInput: $('line-sim-file-input'),
    lineSimTextInput: $('line-sim-text-input'),
    btnLineSimSend: $('btn-line-sim-send'),

    // Toast
    toast: $('toast'),
    toastIcon: $('toast-icon'),
    toastMessage: $('toast-message'),
  };

  function escapeHtml(text) {
    return String(text ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatMoney(num) {
    const val = Number(num || 0);
    return val.toLocaleString('th-TH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function debounce(fn, delayMs) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), delayMs);
    };
  }

  let toastTimer = null;
  function showToast(message, icon = '✅') {
    if (!els.toast) return;
    els.toastIcon.textContent = icon;
    els.toastMessage.textContent = message;
    els.toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      els.toast.classList.remove('show');
    }, 3200);
  }

  function showLoading(show, text = 'น้องโชคกำลังช่วยอ่านเอกสารให้นะครับ...') {
    if (!els.loadingOverlay) return;
    if (els.loadingText) els.loadingText.textContent = text;
    els.loadingOverlay.style.display = show ? 'flex' : 'none';
  }

  function openModal(modalEl) {
    if (modalEl) modalEl.style.display = 'flex';
  }

  function closeModal(modalEl) {
    if (modalEl) modalEl.style.display = 'none';
  }

  async function apiFetch(url, options = {}) {
    const res = await fetch(url, options);
    const contentType = res.headers.get('content-type') || '';
    let data = null;
    if (contentType.includes('application/json')) {
      data = await res.json();
    } else {
      const txt = await res.text();
      data = { detail: txt };
    }
    if (!res.ok) {
      throw new Error(data.detail || data.error || `HTTP ${res.status}`);
    }
    return data;
  }

  function parseTagsList(rawTags) {
    if (Array.isArray(rawTags)) return rawTags.map((t) => String(t).trim()).filter(Boolean);
    return String(rawTags || '')
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean);
  }

  function syncQuickTagButtons() {
    const activeTags = new Set(parseTagsList(els.docTagsInput.value));
    document.querySelectorAll('.btn-quick-tag').forEach((btn) => {
      const tag = btn.dataset.tag || '';
      btn.classList.toggle('active', activeTags.has(tag));
    });
  }

  function syncTechMode() {
    const enabled = Boolean(els.toggleTechMode && els.toggleTechMode.checked);
    document.querySelectorAll('.tech-only').forEach((node) => {
      if (!enabled) {
        node.style.display = 'none';
      } else if (node.classList.contains('btn') || node.classList.contains('toggle-chip')) {
        node.style.display = 'inline-flex';
      } else {
        node.style.display = 'inline-block';
      }
    });

    if (!enabled) {
      const activeTechTab = document.querySelector('.tab-btn.tech-only.active');
      if (activeTechTab) activateTab('tab-text');
    }
    renderBoundingBoxes();
  }

  function activateTab(tabId) {
    document.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    document.querySelectorAll('.tab-content').forEach((pane) => {
      pane.classList.toggle('active', pane.id === tabId);
    });
  }

  function updateCharCount() {
    const txt = els.ocrTextResult.value || '';
    const chars = txt.length;
    const words = txt.trim() ? txt.trim().split(/\s+/).length : 0;
    els.charCount.textContent = `${chars.toLocaleString()} ตัวอักษร | ${words.toLocaleString()} คำ`;
  }

  function renderReviewStatus(reviewStatus) {
    const isDone = reviewStatus === 'reviewed';
    els.reviewStatusPill.className = `badge ${isDone ? 'review-pill-done' : 'review-pill-pending'}`;
    els.reviewStatusPill.textContent = isDone ? '✅ ตรวจยืนยันแล้ว' : '⏳ รอคุณครูตรวจ';
    els.reviewHelperText.textContent = isDone
      ? 'เยี่ยมเลยครับคุณครู ข้อมูลชุดนี้ตรวจเรียบร้อยแล้ว'
      : 'ตรวจดูข้อมูลด้านล่าง ถ้าตรงแล้วกดยืนยันได้เลยครับ';
    els.btnConfirmReview.disabled = !state.currentDocId || isDone;
  }

  function renderLinkedBanners(data) {
    const stu = data && data.student ? data.student : null;
    if (stu) {
      const nick = stu.nickname ? ` (${stu.nickname})` : '';
      const room = stu.classroom ? ` • ${stu.classroom}` : '';
      const phone = stu.parent_phone ? ` • ผู้ปกครอง: ${stu.parent_phone}` : '';
      els.studentBannerText.textContent = `👶 บันทึกเข้าแฟ้มเด็ก: ${stu.full_name || 'นักเรียนใหม่'}${nick}${room}${phone}`;
      const warn = [stu.allergies, stu.medical_notes].filter(Boolean).join(' / ');
      if (warn) {
        els.studentAllergyBadge.textContent = `⚠️ ${warn}`;
        els.studentAllergyBadge.style.display = 'inline-flex';
      } else {
        els.studentAllergyBadge.style.display = 'none';
      }
      els.studentBanner.style.display = 'flex';
    } else {
      els.studentBanner.style.display = 'none';
    }

    const exp = data && data.expense ? data.expense : null;
    if (exp) {
      const reimbStatus = exp.reimbursement_status || 'pending_reimbursement';
      const reimbLabel = REIMB_LABELS[reimbStatus] || reimbStatus;
      const catLabel = EXPENSE_CAT_LABELS[exp.expense_category] || exp.expense_category || 'อื่นๆ';
      els.expenseBannerText.textContent =
        `🧾 ${exp.merchant || 'ร้านค้า'} • ${formatMoney(exp.total_amount)} บาท • ${catLabel} • ${reimbLabel}`;
      els.btnMarkReimbursedInline.style.display =
        reimbStatus === 'pending_reimbursement' ? 'inline-flex' : 'none';
      els.expenseBanner.style.display = 'flex';
    } else {
      els.expenseBanner.style.display = 'none';
    }
  }

  function renderLinesTable(lines) {
    if (!lines || !lines.length) {
      els.linesTbody.innerHTML =
        '<tr><td colspan="3" style="text-align: center; color: var(--text-muted); padding: 2rem;">ยังไม่มีข้อมูลบรรทัด</td></tr>';
      return;
    }
    els.linesTbody.innerHTML = lines
      .map((line, idx) => {
        const conf = Math.round(Number(line.confidence || 0) * 100);
        const badgeCls = conf >= 90 ? 'badge-green' : 'badge-soft';
        return `<tr data-line-idx="${idx}" style="cursor: pointer;">
          <td>${idx + 1}</td>
          <td>${escapeHtml(line.text || '')}</td>
          <td><span class="badge ${badgeCls}">${conf}%</span></td>
        </tr>`;
      })
      .join('');

    els.linesTbody.querySelectorAll('tr[data-line-idx]').forEach((row) => {
      const idx = Number(row.dataset.lineIdx);
      row.addEventListener('mouseenter', () => highlightBox(idx, true));
      row.addEventListener('mouseleave', () => highlightBox(idx, false));
    });
  }

  function highlightBox(idx, active) {
    const box = state.lineBoxes[idx];
    if (box) box.classList.toggle('active', active);
  }

  function renderBoundingBoxes() {
    els.boxesLayer.innerHTML = '';
    state.lineBoxes = [];
    const techEnabled = Boolean(els.toggleTechMode && els.toggleTechMode.checked);
    const boxesOn = Boolean(els.toggleBoxes && els.toggleBoxes.checked);
    if (!techEnabled || !boxesOn || !state.currentLines.length) return;

    const imgEl = els.docImage;
    if (!imgEl || imgEl.style.display === 'none' || !imgEl.naturalWidth) return;

    const refW = Number(state.currentDocData?.image_width || imgEl.naturalWidth || 1);
    const refH = Number(state.currentDocData?.image_height || imgEl.naturalHeight || 1);
    const scaleX = imgEl.clientWidth / refW;
    const scaleY = imgEl.clientHeight / refH;

    state.currentLines.forEach((line, idx) => {
      const pts = line.box;
      if (!Array.isArray(pts) || pts.length < 4) return;
      const xs = pts.map((p) => p[0]);
      const ys = pts.map((p) => p[1]);
      const minX = Math.min(...xs) * scaleX;
      const maxX = Math.max(...xs) * scaleX;
      const minY = Math.min(...ys) * scaleY;
      const maxY = Math.max(...ys) * scaleY;

      const boxEl = document.createElement('div');
      boxEl.className = 'bounding-box';
      boxEl.style.left = `${minX}px`;
      boxEl.style.top = `${minY}px`;
      boxEl.style.width = `${Math.max(4, maxX - minX)}px`;
      boxEl.style.height = `${Math.max(4, maxY - minY)}px`;

      const tip = document.createElement('div');
      tip.className = 'bounding-box-tooltip';
      const pct = Math.round(Number(line.confidence || 0) * 100);
      tip.textContent = `${line.text || ''} (${pct}%)`;
      boxEl.appendChild(tip);

      boxEl.addEventListener('click', () => {
        const row = els.linesTbody.querySelector(`tr[data-line-idx="${idx}"]`);
        if (row) {
          activateTab('tab-lines');
          row.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      });

      els.boxesLayer.appendChild(boxEl);
      state.lineBoxes[idx] = boxEl;
    });
  }

  function buildMarkdownPreview(data) {
    const title = data.filename || 'document';
    const cat = CATEGORY_LABELS[data.category] || data.category || 'general';
    const lines = [
      `# ${title}`,
      `- หมวดหมู่: ${cat}`,
      `- สถานะตรวจ: ${data.review_status === 'reviewed' ? 'ตรวจแล้ว' : 'รอตรวจ'}`,
    ];
    if (data.doc_date) lines.push(`- วันที่เอกสาร: ${data.doc_date}`);
    if (Array.isArray(data.tags) && data.tags.length) lines.push(`- ป้ายกำกับ: ${data.tags.join(', ')}`);
    if (data.expense) {
      lines.push(
        `\n## ข้อมูลเบิกจ่าย\n- ร้านค้า: ${data.expense.merchant}\n- ยอดสุทธิ: ${formatMoney(data.expense.total_amount)} บาท (${REIMB_LABELS[data.expense.reimbursement_status] || ''})`
      );
    }
    if (data.student) {
      lines.push(
        `\n## ทะเบียนเด็ก\n- ชื่อ: ${data.student.full_name} (${data.student.nickname || '-'}) [${data.student.classroom || '-'}]\n- แพ้อาหาร: ${data.student.allergies || 'ไม่มี'}`
      );
    }
    lines.push('\n## ข้อความ OCR\n\n' + (data.text || ''));
    return lines.join('\n');
  }

  function renderDocumentResult(data) {
    if (!data) return;
    const docId = data.doc_id || data.id || null;
    state.currentDocId = docId;
    state.currentDocData = data;
    state.currentLines = Array.isArray(data.lines) ? data.lines : [];
    state.currentIsPdf = Boolean(data.is_pdf || data.file_type === 'pdf');
    state.currentPdfPage = Number(data.current_page || 1);
    state.currentPdfTotalPages = Number(data.total_pages || 1);

    // Image Preview
    const preview = data.image_preview || '';
    if (preview) {
      const src = preview.startsWith('data:') ? preview : `data:image/png;base64,${preview}`;
      els.docImage.onload = () => renderBoundingBoxes();
      els.docImage.src = src;
      els.docImage.style.display = 'block';
      els.viewerPlaceholder.style.display = 'none';
    } else {
      els.docImage.src = '';
      els.docImage.style.display = 'none';
      els.viewerPlaceholder.style.display = 'flex';
    }

    if (data.filename) {
      els.fileBadge.textContent = data.filename;
      els.fileBadge.style.display = 'inline-flex';
    } else {
      els.fileBadge.style.display = 'none';
    }

    if (data.image_width && data.image_height) {
      els.docDimensions.textContent = `${data.image_width} × ${data.image_height} px`;
    } else {
      els.docDimensions.textContent = '';
    }

    // PDF Multi-page bar (only active when user uploaded a multi-page PDF locally)
    if (state.currentIsPdf && state.currentPdfTotalPages > 1 && state.currentFile) {
      els.pdfControls.style.display = 'flex';
      els.pdfPageIndicator.textContent = `หน้า ${state.currentPdfPage} / ${state.currentPdfTotalPages}`;
      els.btnPrevPage.disabled = state.currentPdfPage <= 1;
      els.btnNextPage.disabled = state.currentPdfPage >= state.currentPdfTotalPages;
    } else {
      els.pdfControls.style.display = 'none';
    }

    // Stats Ribbon
    const stats = data.stats || {};
    const durationMs = stats.duration_ms ?? data.duration_ms ?? 0;
    const avgConf = stats.avg_confidence ?? data.avg_confidence ?? 0;
    els.statTime.textContent = durationMs ? `${(Number(durationMs) / 1000).toFixed(2)} วิ` : '-';
    els.statLines.textContent = `${state.currentLines.length} บรรทัด`;
    els.statConf.textContent = avgConf ? `${Math.round(Number(avgConf) * 100)}%` : '-';
    els.statDbStatus.textContent = docId ? 'บันทึกในตู้เซฟแล้ว' : 'ยังไม่บันทึก';

    // Metadata
    els.docCategoryInput.value = data.category || 'general';
    els.docTagsInput.value = parseTagsList(data.tags).join(', ');
    els.docDateInput.value = data.doc_date || '';
    syncQuickTagButtons();

    els.btnSaveDoc.disabled = !docId;
    els.btnExtractExpense.disabled = !docId;

    if (docId && (data.has_source_file || data.source_path)) {
      els.btnDownloadSource.href = `/api/documents/${encodeURIComponent(docId)}/file`;
      els.btnDownloadSource.style.display = 'inline-flex';
    } else {
      els.btnDownloadSource.style.display = 'none';
    }

    renderReviewStatus(data.review_status || 'pending_review');
    renderLinkedBanners(data);

    // Content Tabs
    els.ocrTextResult.value = data.text || '';
    updateCharCount();
    renderLinesTable(state.currentLines);
    els.ocrMarkdownResult.value = buildMarkdownPreview(data);
    try {
      const jsonSnapshot = { ...data };
      if (jsonSnapshot.image_preview) jsonSnapshot.image_preview = '<base64 omitted>';
      els.ocrJsonResult.value = JSON.stringify(jsonSnapshot, null, 2);
    } catch (_) {
      els.ocrJsonResult.value = '';
    }

    els.footerDocId.textContent = docId
      ? `ผู้ช่วย: น้องโชคช่วย • รหัสเอกสาร: ${docId}`
      : 'ผู้ช่วย: น้องโชคช่วย (OpenThai 2.0)';
  }

  async function loadSystemStats() {
    try {
      const [sysRes, watcherRes] = await Promise.allSettled([
        apiFetch('/api/system'),
        apiFetch('/api/watcher/status'),
      ]);

      if (sysRes.status === 'fulfilled') {
        const sys = sysRes.value;
        const db = sys.database || {};
        const pendingReview = Number(db.pending_review_count || 0);
        const pendingReimb = Number(db.pending_reimbursement_total || 0);
        const totalStudents = Number(db.total_students || 0);
        const allergyCount = Number(db.allergy_student_count || 0);
        const totalDocs = Number(db.total_documents || 0);

        els.dbCountBadge.textContent = String(totalDocs);
        els.kpiVaultVal.textContent = `${totalDocs} ฉบับ`;
        els.kpiPendingReviewVal.textContent = `${pendingReview} ฉบับ`;
        els.pendingReimbBadge.textContent = `${formatMoney(pendingReimb)} ฿`;
        els.kpiPendingReimbVal.textContent = `${formatMoney(pendingReimb)} ฿`;
        els.studentCountBadge.textContent = String(totalStudents);
        els.kpiStudentsVal.textContent = `${totalStudents} คน`;
        els.kpiAllergyNote.textContent =
          allergyCount > 0 ? `⚠️ แพ้อาหาร/ระวังพิเศษ ${allergyCount} คน` : 'ยังไม่มีข้อมูลแพ้อาหาร';

        els.llmStatusBadge.textContent = sys.llm_configured
          ? `✨ ใช้ AI ${sys.llm_provider || 'Hybrid'} ช่วยสรุป`
          : '⚙️ อ่านออฟไลน์ในเครื่อง';

        if (els.nongChokSpeech) {
          const tName = sys.teacher_name || 'ครูพิ้งกี้';
          const notes = [];
          if (pendingReview > 0) notes.push(`มีเอกสารรอ${tName}ตรวจ ${pendingReview} ฉบับ`);
          if (pendingReimb > 0) notes.push(`ยอดสำรองจ่ายรอเบิกคืน ${formatMoney(pendingReimb)} บาท`);
          if (allergyCount > 0) notes.push(`ดูแลเด็กแพ้อาหารอยู่ ${allergyCount} คน`);
          els.nongChokSpeech.textContent = notes.length
            ? `สวัสดีครับ${tName}! ตอนนี้ ${notes.join(' • ')} กดที่การ์ดด้านขวาเพื่อดูรายละเอียดได้เลยนะครับ`
            : `สวัสดีครับ${tName}! โยนรูปบิล รูปเอกสารเด็ก หรือ PDF คำสั่งโรงเรียนเข้ามาได้เลยนะครับ เดี๋ยวน้องโชคช่วยจัดระเบียบให้เองครับ`;
        }
      }

      if (watcherRes.status === 'fulfilled') {
        const w = watcherRes.value;
        const inboxCount = Number(w.inbox_pending_count ?? w.pending_count ?? 0);
        els.inboxCountBadge.textContent = String(inboxCount);
      }
    } catch (err) {
      console.warn('Failed to refresh system stats:', err);
    }
  }

  async function uploadAndProcessFile(file, page = 1, processAllPages = false) {
    if (!file) return;
    state.currentFile = file;
    showLoading(true, 'น้องโชคกำลังช่วยอ่านเอกสารและจัดหมวดหมู่ให้นะครับ...');
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('page', String(page));
      formData.append('process_all_pages', String(Boolean(processAllPages)));
      formData.append('save_to_db', 'true');
      formData.append('auto_extract', String(Boolean(els.toggleAutoExtract.checked)));

      const result = await apiFetch('/api/ocr', { method: 'POST', body: formData });
      renderDocumentResult(result);
      await loadSystemStats();
      showToast('น้องโชคอ่านเอกสารและจัดเก็บให้เรียบร้อยแล้วครับ!');
    } catch (err) {
      showToast(`อ่านเอกสารไม่สำเร็จ: ${err.message}`, '❌');
    } finally {
      showLoading(false);
    }
  }

  async function runKindergartenPreset(preset) {
    showLoading(true, 'น้องโชคกำลังโหลดเอกสารตัวอย่างอนุบาลและทดลองอ่านให้ครับ...');
    try {
      state.currentFile = null;
      const result = await apiFetch(`/api/samples/kindergarten/${encodeURIComponent(preset)}`, {
        method: 'POST',
      });
      renderDocumentResult(result);
      await loadSystemStats();
      showToast('เปิดเอกสารตัวอย่างเรียบร้อยครับ ลองตรวจและกดยืนยันได้เลย!');
    } catch (err) {
      showToast(`โหลดเอกสารตัวอย่างไม่สำเร็จ: ${err.message}`, '❌');
    } finally {
      showLoading(false);
    }
  }

  async function runLegacySample(filename) {
    showLoading(true, 'น้องโชคกำลังหยิบเอกสารตัวอย่างมาอ่านให้ครับ...');
    try {
      const res = await fetch(`/api/sample/${encodeURIComponent(filename)}`);
      if (!res.ok) throw new Error('ไม่พบไฟล์ตัวอย่าง');
      const blob = await res.blob();
      const file = new File([blob], filename, { type: blob.type || 'image/png' });
      await uploadAndProcessFile(file, 1, false);
    } catch (err) {
      showLoading(false);
      showToast(`โหลดไฟล์ตัวอย่างไม่สำเร็จ: ${err.message}`, '❌');
    }
  }

  async function openDocumentById(docId) {
    if (!docId) return;
    showLoading(true, 'น้องโชคกำลังหยิบเอกสารจากตู้เซฟ...');
    try {
      const res = await apiFetch(`/api/documents/${encodeURIComponent(docId)}`);
      state.currentFile = null;
      renderDocumentResult(res.document);
      closeModal(els.dbModal);
      closeModal(els.expenseModal);
      closeModal(els.studentModal);
      closeModal(els.lineSimModal);
    } catch (err) {
      showToast(`เปิดเอกสารไม่ได้: ${err.message}`, '❌');
    } finally {
      showLoading(false);
    }
  }

  async function saveCurrentDocument() {
    if (!state.currentDocId) return;
    try {
      const payload = {
        text: els.ocrTextResult.value,
        category: els.docCategoryInput.value,
        tags: els.docTagsInput.value,
        doc_date: els.docDateInput.value,
      };
      const res = await apiFetch(`/api/documents/${encodeURIComponent(state.currentDocId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      renderDocumentResult(res.document);
      await loadSystemStats();
      showToast('บันทึกการแก้ไขข้อความและป้ายกำกับแล้วครับ');
    } catch (err) {
      showToast(`บันทึกไม่สำเร็จ: ${err.message}`, '❌');
    }
  }

  async function confirmCurrentReview() {
    if (!state.currentDocId) return;
    try {
      const res = await apiFetch(`/api/documents/${encodeURIComponent(state.currentDocId)}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ review_status: 'reviewed' }),
      });
      renderDocumentResult(res.document);
      await loadSystemStats();
      showToast('ยืนยันตรวจเอกสารเรียบร้อยครับคุณครู!');
    } catch (err) {
      showToast(`ยืนยันไม่สำเร็จ: ${err.message}`, '❌');
    }
  }

  async function markCurrentExpenseReimbursed() {
    if (!state.currentDocId || !state.currentDocData?.expense) return;
    const exp = state.currentDocData.expense;
    try {
      await apiFetch(`/api/expenses/${encodeURIComponent(state.currentDocId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          merchant: exp.merchant || '',
          expense_date: exp.expense_date || '',
          subtotal: Number(exp.subtotal || 0),
          vat_amount: Number(exp.vat_amount || 0),
          total_amount: Number(exp.total_amount || 0),
          currency: exp.currency || 'THB',
          expense_category: exp.expense_category || 'classroom',
          reimbursement_status: 'reimbursed',
          budget_source: exp.budget_source || 'classroom',
        }),
      });
      const docRes = await apiFetch(`/api/documents/${encodeURIComponent(state.currentDocId)}`);
      renderDocumentResult(docRes.document);
      await loadSystemStats();
      showToast('อัปเดตสถานะเป็น "เบิกคืนแล้ว" เรียบร้อยครับ!');
    } catch (err) {
      showToast(`อัปเดตสถานะไม่สำเร็จ: ${err.message}`, '❌');
    }
  }

  async function runExtractIntelligence() {
    if (!state.currentDocId) return;
    showLoading(true, 'น้องโชคกำลังดึงยอดบิลและข้อมูลเด็กให้อีกรอบครับ...');
    try {
      const res = await apiFetch(`/api/documents/${encodeURIComponent(state.currentDocId)}/extract`, {
        method: 'POST',
      });
      if (res.document) {
        renderDocumentResult(res.document);
      }
      await loadSystemStats();
      if (res.student) {
        showToast('ดึงข้อมูลประวัติเด็กอนุบาลเข้าแฟ้มเรียบร้อยครับ!', '👶');
      } else if (res.expense) {
        showToast(`สรุปยอดบิล ${formatMoney(res.expense.total_amount)} บาทเรียบร้อยครับ!`, '🧾');
      } else {
        showToast('วิเคราะห์ข้อมูลเสร็จเรียบร้อยครับ');
      }
    } catch (err) {
      showToast(`สกัดข้อมูลไม่สำเร็จ: ${err.message}`, '❌');
    } finally {
      showLoading(false);
    }
  }

  // Vault Modal
  async function loadVaultDocuments() {
    const params = new URLSearchParams();
    if (els.dbSearchInput.value.trim()) params.set('query', els.dbSearchInput.value.trim());
    if (els.dbReviewFilter.value) params.set('review_status', els.dbReviewFilter.value);
    if (els.dbCategoryFilter.value) params.set('category', els.dbCategoryFilter.value);
    if (els.dbTagFilter.value.trim()) params.set('tag', els.dbTagFilter.value.trim());

    try {
      const data = await apiFetch(`/api/documents?${params.toString()}`);
      const docs = data.documents || [];
      const dbStats = data.db_stats || {};
      els.dbSummaryStats.textContent =
        `พบเอกสาร ${docs.length} ฉบับ จากทั้งหมด ${dbStats.total_documents || 0} ฉบับในตู้เซฟ (รอตรวจ ${dbStats.pending_review_count || 0} ฉบับ)`;

      if (!docs.length) {
        els.docsList.innerHTML =
          '<div style="text-align:center; padding: 2.5rem; color: var(--text-muted);">ไม่พบเอกสารที่ตรงกับเงื่อนไขครับคุณครู</div>';
        return;
      }

      els.docsList.innerHTML = docs
        .map((doc) => {
          const isDone = doc.review_status === 'reviewed';
          const reviewBadge = isDone
            ? '<span class="badge review-pill-done">✅ ตรวจแล้ว</span>'
            : '<span class="badge review-pill-pending">⏳ รอคุณครูตรวจ</span>';
          const catLabel = CATEGORY_LABELS[doc.category] || doc.category || 'general';
          const channelBadge =
            doc.ingestion_channel === 'line_oa' ? '<span class="badge badge-soft">💬 LINE OA</span>' : '';
          const tagsHtml = (doc.tags || [])
            .map((t) => `<span class="tag-pill">${escapeHtml(t)}</span>`)
            .join(' ');
          const snippet = escapeHtml((doc.text || doc.snippet || '').replace(/\s+/g, ' ').slice(0, 180));
          return `<div class="doc-card">
            <div class="doc-card-info">
              <div class="doc-card-title">
                <span>${escapeHtml(doc.filename || 'ไม่ระบุชื่อไฟล์')}</span>
                ${reviewBadge}
                ${channelBadge}
              </div>
              <div class="doc-card-meta">
                <span>${escapeHtml(catLabel)}</span>
                <span>📅 ${escapeHtml(doc.doc_date || (doc.created_at || '').slice(0, 10))}</span>
                ${tagsHtml}
              </div>
              <div class="doc-card-snippet">${snippet || '(ไม่มีข้อความ)'}</div>
            </div>
            <div class="doc-card-actions">
              <button type="button" class="btn btn-primary btn-sm doc-open-btn" data-doc-id="${escapeHtml(doc.id)}">📄 เปิดดู</button>
              ${
                isDone
                  ? ''
                  : `<button type="button" class="btn btn-clover btn-sm doc-review-btn" data-doc-id="${escapeHtml(doc.id)}">✅ ตรวจแล้ว</button>`
              }
              <button type="button" class="btn btn-danger btn-sm doc-del-btn" data-doc-id="${escapeHtml(doc.id)}">🗑️ ลบ</button>
            </div>
          </div>`;
        })
        .join('');

      els.docsList.querySelectorAll('.doc-open-btn').forEach((btn) => {
        btn.addEventListener('click', () => openDocumentById(btn.dataset.docId));
      });
      els.docsList.querySelectorAll('.doc-review-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
          await apiFetch(`/api/documents/${encodeURIComponent(btn.dataset.docId)}/review`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ review_status: 'reviewed' }),
          });
          await loadVaultDocuments();
          await loadSystemStats();
          if (state.currentDocId === btn.dataset.docId) {
            renderReviewStatus('reviewed');
          }
          showToast('ยืนยันตรวจเอกสารแล้วครับ');
        });
      });
      els.docsList.querySelectorAll('.doc-del-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const targetId = btn.dataset.docId;
          await apiFetch(`/api/documents/${encodeURIComponent(targetId)}`, { method: 'DELETE' });
          if (state.currentDocId === targetId) state.currentDocId = null;
          await loadVaultDocuments();
          await loadSystemStats();
          showToast('ลบเอกสารออกจากตู้เซฟแล้วครับ', '🗑️');
        });
      });
    } catch (err) {
      showToast(`เปิดตู้เซฟไม่สำเร็จ: ${err.message}`, '❌');
    }
  }

  // Expense Reimbursement Modal
  function syncExpenseExportUrl() {
    const params = new URLSearchParams();
    if (els.expenseMonthFilter.value) params.set('month', els.expenseMonthFilter.value);
    if (els.expenseReimbFilter.value) params.set('reimbursement_status', els.expenseReimbFilter.value);
    if (els.expenseCategoryFilter.value) params.set('expense_category', els.expenseCategoryFilter.value);
    if (els.expenseTagFilter.value) params.set('tag', els.expenseTagFilter.value);
    const qs = params.toString();
    els.btnExportExpensesCsv.href = qs ? `/api/expenses/export.csv?${qs}` : '/api/expenses/export.csv';
  }

  async function loadExpensesLedger() {
    syncExpenseExportUrl();
    const params = new URLSearchParams();
    if (els.expenseMonthFilter.value) params.set('month', els.expenseMonthFilter.value);
    if (els.expenseReimbFilter.value) params.set('reimbursement_status', els.expenseReimbFilter.value);
    if (els.expenseCategoryFilter.value) params.set('expense_category', els.expenseCategoryFilter.value);
    if (els.expenseTagFilter.value) params.set('tag', els.expenseTagFilter.value);

    try {
      const data = await apiFetch(`/api/expenses?${params.toString()}`);
      const summary = data.summary || {};
      const expenses = data.expenses || [];

      els.expKpiPendingReimb.textContent = formatMoney(summary.pending_reimbursement_total);
      els.expKpiCount.textContent = String(summary.count || 0);
      els.expKpiSubtotal.textContent = formatMoney(summary.subtotal);
      els.expKpiVat.textContent = formatMoney(summary.vat_amount);
      els.expKpiTotal.textContent = formatMoney(summary.total_amount);

      if (!expenses.length) {
        els.expensesTbody.innerHTML =
          '<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 2rem;">ยังไม่มีรายการเบิกจ่ายตามตัวกรองนี้</td></tr>';
        return;
      }

      els.expensesTbody.innerHTML = expenses
        .map((exp, idx) => {
          const catLabel = EXPENSE_CAT_LABELS[exp.expense_category] || exp.expense_category || 'other';
          const reimbVal = exp.reimbursement_status || 'pending_reimbursement';
          const tagsStr = (exp.tags || []).map((t) => `<span class="tag-pill">${escapeHtml(t)}</span>`).join(' ');
          return `<tr data-exp-idx="${idx}">
            <td>${escapeHtml(exp.expense_date || '-')}</td>
            <td>
              <div style="font-weight: 600;">${escapeHtml(exp.merchant || 'ไม่ระบุร้านค้า')}</div>
              <div style="display:flex; gap:0.25rem; flex-wrap:wrap; margin-top:2px;">${tagsStr}</div>
            </td>
            <td>${escapeHtml(catLabel)}</td>
            <td>
              <select class="meta-input exp-reimb-select" data-doc-id="${escapeHtml(exp.document_id)}" data-idx="${idx}" style="min-width: 155px;">
                <option value="pending_reimbursement" ${reimbVal === 'pending_reimbursement' ? 'selected' : ''}>💸 รอเบิกคืนโรงเรียน</option>
                <option value="reimbursed" ${reimbVal === 'reimbursed' ? 'selected' : ''}>✅ เบิกคืนแล้ว</option>
                <option value="personal" ${reimbVal === 'personal' ? 'selected' : ''}>👤 จ่ายส่วนตัว</option>
              </select>
            </td>
            <td>${formatMoney(exp.subtotal)}</td>
            <td>${formatMoney(exp.vat_amount)}</td>
            <td style="font-weight: 700; color: #b45309;">${formatMoney(exp.total_amount)}</td>
            <td>
              <div style="display:flex; gap:0.25rem;">
                <button type="button" class="btn btn-secondary btn-sm exp-open-doc" data-doc-id="${escapeHtml(exp.document_id)}">ดูบิล</button>
                <button type="button" class="btn btn-danger btn-sm exp-del-btn" data-doc-id="${escapeHtml(exp.document_id)}">ลบ</button>
              </div>
            </td>
          </tr>`;
        })
        .join('');

      els.expensesTbody.querySelectorAll('.exp-reimb-select').forEach((sel) => {
        sel.addEventListener('change', async () => {
          const item = expenses[Number(sel.dataset.idx)];
          if (!item) return;
          await apiFetch(`/api/expenses/${encodeURIComponent(item.document_id)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              merchant: item.merchant || '',
              expense_date: item.expense_date || '',
              subtotal: Number(item.subtotal || 0),
              vat_amount: Number(item.vat_amount || 0),
              total_amount: Number(item.total_amount || 0),
              currency: item.currency || 'THB',
              expense_category: item.expense_category || 'other',
              reimbursement_status: sel.value,
              budget_source: sel.value === 'personal' ? 'personal' : item.budget_source || 'classroom',
            }),
          });
          await loadExpensesLedger();
          await loadSystemStats();
          if (state.currentDocId === item.document_id) {
            const fresh = await apiFetch(`/api/documents/${encodeURIComponent(item.document_id)}`);
            renderDocumentResult(fresh.document);
          }
          showToast('อัปเดตสถานะการเบิกจ่ายเรียบร้อยครับ');
        });
      });

      els.expensesTbody.querySelectorAll('.exp-open-doc').forEach((btn) => {
        btn.addEventListener('click', () => openDocumentById(btn.dataset.docId));
      });
      els.expensesTbody.querySelectorAll('.exp-del-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
          await apiFetch(`/api/expenses/${encodeURIComponent(btn.dataset.docId)}`, { method: 'DELETE' });
          await loadExpensesLedger();
          await loadSystemStats();
          showToast('ลบรายการเบิกจ่ายแล้วครับ', '🗑️');
        });
      });
    } catch (err) {
      showToast(`โหลดสมุดเบิกจ่ายไม่สำเร็จ: ${err.message}`, '❌');
    }
  }

  // Kindergarten Student Roster Modal
  function syncStudentExportUrl() {
    const params = new URLSearchParams();
    if (els.studentSearchInput.value.trim()) params.set('query', els.studentSearchInput.value.trim());
    if (els.studentClassroomFilter.value.trim()) params.set('classroom', els.studentClassroomFilter.value.trim());
    if (els.studentAllergyOnly.checked) params.set('has_allergy', 'true');
    const qs = params.toString();
    els.btnExportStudentsCsv.href = qs ? `/api/students/export.csv?${qs}` : '/api/students/export.csv';
  }

  function resetStudentForm() {
    els.stuEditId.value = '';
    els.stuFullName.value = '';
    els.stuNickname.value = '';
    els.stuCitizenId.value = '';
    els.stuBirthDate.value = '';
    els.stuClassroom.value = 'อนุบาล';
    els.stuParentName.value = '';
    els.stuParentPhone.value = '';
    els.stuAllergies.value = '';
    els.stuMedical.value = '';
  }

  function populateStudentForm(stu) {
    els.stuEditId.value = stu.id || '';
    els.stuFullName.value = stu.full_name || '';
    els.stuNickname.value = stu.nickname || '';
    els.stuCitizenId.value = stu.citizen_id || '';
    els.stuBirthDate.value = stu.birth_date || '';
    els.stuClassroom.value = stu.classroom || 'อนุบาล';
    els.stuParentName.value = stu.parent_name || '';
    els.stuParentPhone.value = stu.parent_phone || '';
    els.stuAllergies.value = stu.allergies || '';
    els.stuMedical.value = stu.medical_notes || '';
    els.studentForm.style.display = 'block';
    els.stuFullName.focus();
  }

  async function loadStudentsRoster() {
    syncStudentExportUrl();
    const params = new URLSearchParams();
    if (els.studentSearchInput.value.trim()) params.set('query', els.studentSearchInput.value.trim());
    if (els.studentClassroomFilter.value.trim()) params.set('classroom', els.studentClassroomFilter.value.trim());
    if (els.studentAllergyOnly.checked) params.set('has_allergy', 'true');

    try {
      const data = await apiFetch(`/api/students?${params.toString()}`);
      const students = data.students || [];
      const summary = data.summary || {};

      els.stuKpiTotal.textContent = `${summary.total_students || 0} คน`;
      els.stuKpiAllergy.textContent = `${summary.allergy_count || 0} คน`;
      const rooms = Object.keys(summary.by_classroom || {});
      els.stuKpiRooms.textContent = rooms.length ? rooms.join(', ') : '-';

      if (!students.length) {
        els.studentsList.innerHTML =
          '<div style="text-align:center; padding:2.5rem; color:var(--text-muted);">ยังไม่มีข้อมูลเด็กในห้องเรียน ลองกด "+ เพิ่มชื่อเด็ก" หรือสแกนใบสมัครนักเรียนดูนะครับ</div>';
        return;
      }

      els.studentsList.innerHTML = students
        .map((stu, idx) => {
          const nick = stu.nickname ? ` (${escapeHtml(stu.nickname)})` : '';
          const warnParts = [stu.allergies, stu.medical_notes].filter(Boolean);
          const warnBadge = warnParts.length
            ? `<span class="badge badge-allergy">⚠️ ${escapeHtml(warnParts.join(' / '))}</span>`
            : '<span class="badge badge-green">ปกติ ไม่มีแพ้อาหาร</span>';
          const docBtn = stu.document_id
            ? `<button type="button" class="btn btn-secondary btn-sm stu-open-doc" data-doc-id="${escapeHtml(stu.document_id)}">📄 ดูใบต้นฉบับ</button>`
            : '';
          return `<div class="doc-card">
            <div class="doc-card-info">
              <div class="doc-card-title">
                <span>👶 ${escapeHtml(stu.full_name || 'ไม่ระบุชื่อ')}${nick}</span>
                <span class="badge badge-sky-pill">${escapeHtml(stu.classroom || 'อนุบาล')}</span>
                ${warnBadge}
              </div>
              <div class="doc-card-meta">
                <span>🪪 เลข 13 หลัก: ${escapeHtml(stu.citizen_id || '-')}</span>
                <span>🎂 วันเกิด: ${escapeHtml(stu.birth_date || '-')}</span>
              </div>
              <div class="doc-card-meta">
                <span>👨‍👩‍👧 ผู้ปกครอง: ${escapeHtml(stu.parent_name || '-')}</span>
                <span>📞 โทร: <strong>${escapeHtml(stu.parent_phone || '-')}</strong></span>
              </div>
            </div>
            <div class="doc-card-actions">
              <button type="button" class="btn btn-primary btn-sm stu-edit-btn" data-idx="${idx}">✏️ แก้ไข</button>
              ${docBtn}
              <button type="button" class="btn btn-danger btn-sm stu-del-btn" data-id="${escapeHtml(stu.id)}">🗑️ ลบ</button>
            </div>
          </div>`;
        })
        .join('');

      els.studentsList.querySelectorAll('.stu-edit-btn').forEach((btn) => {
        btn.addEventListener('click', () => populateStudentForm(students[Number(btn.dataset.idx)]));
      });
      els.studentsList.querySelectorAll('.stu-open-doc').forEach((btn) => {
        btn.addEventListener('click', () => openDocumentById(btn.dataset.docId));
      });
      els.studentsList.querySelectorAll('.stu-del-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
          await apiFetch(`/api/students/${encodeURIComponent(btn.dataset.id)}`, { method: 'DELETE' });
          await loadStudentsRoster();
          await loadSystemStats();
          showToast('ลบข้อมูลเด็กออกจากแฟ้มแล้วครับ', '🗑️');
        });
      });
    } catch (err) {
      showToast(`โหลดแฟ้มเด็กไม่สำเร็จ: ${err.message}`, '❌');
    }
  }

  async function submitStudentForm(evt) {
    evt.preventDefault();
    const editId = els.stuEditId.value.trim();
    const payload = {
      full_name: els.stuFullName.value.trim(),
      nickname: els.stuNickname.value.trim(),
      citizen_id: els.stuCitizenId.value.trim(),
      birth_date: els.stuBirthDate.value,
      classroom: els.stuClassroom.value.trim() || 'อนุบาล',
      parent_name: els.stuParentName.value.trim(),
      parent_phone: els.stuParentPhone.value.trim(),
      allergies: els.stuAllergies.value.trim(),
      medical_notes: els.stuMedical.value.trim(),
    };
    try {
      if (editId) {
        await apiFetch(`/api/students/${encodeURIComponent(editId)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        showToast('อัปเดตข้อมูลนักเรียนเรียบร้อยครับ');
      } else {
        await apiFetch('/api/students', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        showToast('เพิ่มรายชื่อเด็กใหม่เข้าแฟ้มแล้วครับ');
      }
      resetStudentForm();
      els.studentForm.style.display = 'none';
      await loadStudentsRoster();
      await loadSystemStats();
    } catch (err) {
      showToast(`บันทึกข้อมูลเด็กไม่สำเร็จ: ${err.message}`, '❌');
    }
  }

  // Interactive LINE OA Simulator
  function scrollLineChatBottom() {
    if (els.lineChatMessages) {
      els.lineChatMessages.scrollTop = els.lineChatMessages.scrollHeight;
    }
  }

  function appendLineUserMessage(text, imageUrl = '') {
    const row = document.createElement('div');
    row.className = 'line-msg line-msg-user';
    const bubble = document.createElement('div');
    bubble.className = 'line-bubble line-bubble-user';
    if (imageUrl) {
      const img = document.createElement('img');
      img.src = imageUrl;
      img.alt = 'ภาพที่ส่งเข้า LINE';
      img.style.maxWidth = '180px';
      img.style.borderRadius = '8px';
      img.style.display = 'block';
      img.style.marginBottom = text ? '6px' : '0';
      bubble.appendChild(img);
    }
    if (text) {
      const span = document.createElement('span');
      span.textContent = text;
      bubble.appendChild(span);
    }
    row.appendChild(bubble);
    els.lineChatMessages.appendChild(row);
    scrollLineChatBottom();
  }

  function appendLineBotResponse(res) {
    const row = document.createElement('div');
    row.className = 'line-msg line-msg-bot';

    const avatar = document.createElement('img');
    avatar.src = '/static/mascot.jpg';
    avatar.alt = 'น้องโชค';
    avatar.className = 'line-msg-avatar';
    row.appendChild(avatar);

    const col = document.createElement('div');
    col.style.display = 'flex';
    col.style.flexDirection = 'column';
    col.style.gap = '0.45rem';

    if (res.flex_message && res.flex_message.contents) {
      const contents = res.flex_message.contents;
      const headText = contents.header?.contents?.[0]?.text || 'น้องโชคช่วยสรุปให้ครับ';
      const bodyLines = (contents.body?.contents || []).map((c) => c.text).filter(Boolean);

      const card = document.createElement('div');
      card.className = 'line-flex-card';

      const headEl = document.createElement('div');
      headEl.className = 'line-flex-head';
      headEl.textContent = headText;
      card.appendChild(headEl);

      const bodyEl = document.createElement('div');
      bodyEl.className = 'line-flex-body';
      bodyLines.forEach((line) => {
        const div = document.createElement('div');
        div.textContent = line;
        bodyEl.appendChild(div);
      });
      card.appendChild(bodyEl);

      if (Array.isArray(res.actions) && res.actions.length) {
        const footerEl = document.createElement('div');
        footerEl.className = 'line-flex-footer';
        res.actions.forEach((act) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = `btn btn-sm ${act.action === 'confirm_review' ? 'btn-primary' : 'btn-secondary'}`;
          btn.textContent = act.label;
          btn.addEventListener('click', () => handleLineSimAction(act, btn));
          footerEl.appendChild(btn);
        });
        card.appendChild(footerEl);
      }

      col.appendChild(card);
    } else if (res.reply_text) {
      const bubble = document.createElement('div');
      bubble.className = 'line-bubble line-bubble-bot';
      bubble.textContent = res.reply_text;
      col.appendChild(bubble);
    }

    row.appendChild(col);
    els.lineChatMessages.appendChild(row);
    scrollLineChatBottom();
  }

  async function handleLineSimAction(act, btnEl) {
    if (act.action === 'open_doc' && act.doc_id) {
      await openDocumentById(act.doc_id);
      return;
    }
    if (!act.data) return;
    if (btnEl) btnEl.disabled = true;
    try {
      const fd = new FormData();
      fd.append('postback_data', act.data);
      const res = await apiFetch('/api/line/simulate', { method: 'POST', body: fd });
      appendLineBotResponse(res);
      await loadSystemStats();
      if (state.currentDocId && state.currentDocId === act.doc_id) {
        const docRes = await apiFetch(`/api/documents/${encodeURIComponent(act.doc_id)}`);
        renderDocumentResult(docRes.document);
      }
    } catch (err) {
      showToast(`ส่งคำสั่งไม่สำเร็จ: ${err.message}`, '❌');
    }
  }

  async function sendLineSimText(text) {
    const clean = (text || '').trim();
    if (!clean) return;
    appendLineUserMessage(clean);
    try {
      const fd = new FormData();
      fd.append('text', clean);
      const res = await apiFetch('/api/line/simulate', { method: 'POST', body: fd });
      appendLineBotResponse(res);
    } catch (err) {
      appendLineBotResponse({ reply_text: `น้องโชคติดขัดนิดหน่อยครับคุณครู: ${err.message}` });
    }
  }

  async function sendLineSimFile(file, label = '') {
    if (!file) return;
    const isImg = (file.type || '').startsWith('image/');
    const previewUrl = isImg ? URL.createObjectURL(file) : '';
    appendLineUserMessage(label || `ส่งไฟล์: ${file.name}`, previewUrl);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await apiFetch('/api/line/simulate', { method: 'POST', body: fd });
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      appendLineBotResponse(res);
      if (res.ocr) renderDocumentResult(res.ocr);
      await loadSystemStats();
    } catch (err) {
      appendLineBotResponse({ reply_text: `น้องโชคอ่านไฟล์นี้ไม่สำเร็จครับ: ${err.message}` });
    }
  }

  async function sendLineSimPreset(preset) {
    const fname = PRESET_TO_FILE[preset];
    if (!fname) return;
    try {
      const res = await fetch(`/api/sample/${encodeURIComponent(fname)}`);
      if (!res.ok) throw new Error('ไม่พบไฟล์ตัวอย่าง');
      const blob = await res.blob();
      const file = new File([blob], fname, { type: blob.type || 'image/png' });
      await sendLineSimFile(file, PRESET_LABELS[preset] || fname);
    } catch (err) {
      showToast(`ส่งตัวอย่างไม่สำเร็จ: ${err.message}`, '❌');
    }
  }

  function bindEvents() {
    // Tabs & Tech Mode
    document.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => activateTab(btn.dataset.tab));
    });
    els.toggleTechMode?.addEventListener('change', syncTechMode);
    els.toggleBoxes?.addEventListener('change', renderBoundingBoxes);
    window.addEventListener('resize', debounce(renderBoundingBoxes, 120));

    // File Upload & Drag-Drop
    els.btnSelectFile?.addEventListener('click', () => els.fileInput.click());
    els.dropzone?.addEventListener('click', () => els.fileInput.click());
    els.fileInput?.addEventListener('change', (e) => {
      if (e.target.files?.[0]) uploadAndProcessFile(e.target.files[0], 1, false);
      e.target.value = '';
    });
    els.dropzone?.addEventListener('dragover', (e) => {
      e.preventDefault();
      els.dropzone.classList.add('dragover');
    });
    els.dropzone?.addEventListener('dragleave', () => els.dropzone.classList.remove('dragover'));
    els.dropzone?.addEventListener('drop', (e) => {
      e.preventDefault();
      els.dropzone.classList.remove('dragover');
      if (e.dataTransfer?.files?.[0]) uploadAndProcessFile(e.dataTransfer.files[0], 1, false);
    });

    // Presets & Sample Buttons
    document.querySelectorAll('.btn-preset').forEach((btn) => {
      btn.addEventListener('click', () => runKindergartenPreset(btn.dataset.preset));
    });
    document.querySelectorAll('.btn-sample[data-sample]').forEach((btn) => {
      btn.addEventListener('click', () => runLegacySample(btn.dataset.sample));
    });

    // PDF Paging
    els.btnPrevPage?.addEventListener('click', () => {
      if (state.currentFile && state.currentPdfPage > 1) {
        uploadAndProcessFile(state.currentFile, state.currentPdfPage - 1, false);
      }
    });
    els.btnNextPage?.addEventListener('click', () => {
      if (state.currentFile && state.currentPdfPage < state.currentPdfTotalPages) {
        uploadAndProcessFile(state.currentFile, state.currentPdfPage + 1, false);
      }
    });
    els.btnOcrAllPages?.addEventListener('click', () => {
      if (state.currentFile) uploadAndProcessFile(state.currentFile, 1, true);
    });

    // Review & Document Actions
    els.ocrTextResult?.addEventListener('input', updateCharCount);
    els.docTagsInput?.addEventListener('input', syncQuickTagButtons);
    document.querySelectorAll('.btn-quick-tag').forEach((btn) => {
      btn.addEventListener('click', () => {
        const tag = btn.dataset.tag || '';
        const tags = parseTagsList(els.docTagsInput.value);
        const idx = tags.indexOf(tag);
        if (idx >= 0) tags.splice(idx, 1);
        else tags.push(tag);
        els.docTagsInput.value = tags.join(', ');
        syncQuickTagButtons();
      });
    });

    els.btnSaveDoc?.addEventListener('click', saveCurrentDocument);
    els.btnConfirmReview?.addEventListener('click', confirmCurrentReview);
    els.btnExtractExpense?.addEventListener('click', runExtractIntelligence);
    els.btnMarkReimbursedInline?.addEventListener('click', markCurrentExpenseReimbursed);

    els.btnViewInStudents?.addEventListener('click', () => {
      openModal(els.studentModal);
      loadStudentsRoster();
    });
    els.btnViewInExpenses?.addEventListener('click', () => {
      openModal(els.expenseModal);
      loadExpensesLedger();
    });

    // Copy & Downloads
    els.btnCopy?.addEventListener('click', async () => {
      const txt = els.ocrTextResult.value || '';
      if (!txt) return showToast('ยังไม่มีข้อความให้คัดลอกครับ', 'ℹ️');
      try {
        await navigator.clipboard.writeText(txt);
        showToast('คัดลอกข้อความเรียบร้อยครับ!');
      } catch (_) {
        els.ocrTextResult.select();
        document.execCommand('copy');
        showToast('คัดลอกข้อความเรียบร้อยครับ!');
      }
    });

    els.btnDownloadTxt?.addEventListener('click', () => {
      const txt = els.ocrTextResult.value || '';
      const blob = new Blob([txt], { type: 'text/plain;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${state.currentDocData?.filename || 'nong-chok-ocr'}.txt`;
      a.click();
      URL.revokeObjectURL(a.href);
    });

    els.btnDownloadJson?.addEventListener('click', () => {
      const raw = els.ocrJsonResult.value || '{}';
      const blob = new Blob([raw], { type: 'application/json;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${state.currentDocData?.filename || 'nong-chok-ocr'}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    });

    // Inbox Scan
    els.btnScanInbox?.addEventListener('click', async () => {
      showLoading(true, 'น้องโชคกำลังไปดูดไฟล์จากโฟลเดอร์ inbox ครับ...');
      try {
        const res = await apiFetch('/api/watcher/scan', { method: 'POST' });
        await loadSystemStats();
        const cnt = Number(res.processed || res.ingested_count || 0);
        showToast(cnt > 0 ? `สแกนไฟล์เข้าตู้เซฟแล้ว ${cnt} ฉบับครับ!` : 'ยังไม่มีไฟล์ใหม่ในโฟลเดอร์ inbox ครับ');
      } catch (err) {
        showToast(`สแกนไม่สำเร็จ: ${err.message}`, '❌');
      } finally {
        showLoading(false);
      }
    });

    // Header & Hero Modals
    els.btnOpenDb?.addEventListener('click', () => {
      openModal(els.dbModal);
      loadVaultDocuments();
    });
    els.kpiCardVault?.addEventListener('click', () => {
      els.dbReviewFilter.value = '';
      els.dbCategoryFilter.value = '';
      els.dbTagFilter.value = '';
      els.dbSearchInput.value = '';
      openModal(els.dbModal);
      loadVaultDocuments();
    });
    els.kpiCardPendingReview?.addEventListener('click', () => {
      els.dbReviewFilter.value = 'pending_review';
      els.dbCategoryFilter.value = '';
      els.dbTagFilter.value = '';
      els.dbSearchInput.value = '';
      openModal(els.dbModal);
      loadVaultDocuments();
    });

    els.btnOpenExpenses?.addEventListener('click', () => {
      openModal(els.expenseModal);
      loadExpensesLedger();
    });
    els.kpiCardPendingReimb?.addEventListener('click', () => {
      els.expenseReimbFilter.value = 'pending_reimbursement';
      els.expenseMonthFilter.value = '';
      els.expenseCategoryFilter.value = '';
      els.expenseTagFilter.value = '';
      openModal(els.expenseModal);
      loadExpensesLedger();
    });

    els.btnOpenStudents?.addEventListener('click', () => {
      openModal(els.studentModal);
      loadStudentsRoster();
    });
    els.kpiCardStudents?.addEventListener('click', () => {
      openModal(els.studentModal);
      loadStudentsRoster();
    });

    els.btnOpenLineSim?.addEventListener('click', () => openModal(els.lineSimModal));

    // Close Modal Buttons & Backdrops
    [
      [els.btnCloseDb, els.dbModal],
      [els.btnCloseExpenses, els.expenseModal],
      [els.btnCloseStudents, els.studentModal],
      [els.btnCloseLineSim, els.lineSimModal],
    ].forEach(([btn, modal]) => {
      btn?.addEventListener('click', () => closeModal(modal));
      modal?.addEventListener('click', (e) => {
        if (e.target === modal) closeModal(modal);
      });
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        [els.dbModal, els.expenseModal, els.studentModal, els.lineSimModal].forEach(closeModal);
      }
    });

    // Vault Filters
    els.dbSearchInput?.addEventListener('input', debounce(loadVaultDocuments, 220));
    els.dbTagFilter?.addEventListener('input', debounce(loadVaultDocuments, 220));
    els.dbReviewFilter?.addEventListener('change', loadVaultDocuments);
    els.dbCategoryFilter?.addEventListener('change', loadVaultDocuments);
    els.btnClearSearch?.addEventListener('click', () => {
      els.dbSearchInput.value = '';
      els.dbReviewFilter.value = '';
      els.dbCategoryFilter.value = '';
      els.dbTagFilter.value = '';
      loadVaultDocuments();
    });

    // Expense Filters
    [
      els.expenseMonthFilter,
      els.expenseReimbFilter,
      els.expenseCategoryFilter,
      els.expenseTagFilter,
    ].forEach((el) => el?.addEventListener('change', loadExpensesLedger));
    els.btnClearExpenseFilters?.addEventListener('click', () => {
      els.expenseMonthFilter.value = '';
      els.expenseReimbFilter.value = '';
      els.expenseCategoryFilter.value = '';
      els.expenseTagFilter.value = '';
      loadExpensesLedger();
    });

    // Student Filters & Form
    els.studentSearchInput?.addEventListener('input', debounce(loadStudentsRoster, 220));
    els.studentClassroomFilter?.addEventListener('input', debounce(loadStudentsRoster, 220));
    els.studentAllergyOnly?.addEventListener('change', loadStudentsRoster);
    els.btnClearStudentFilters?.addEventListener('click', () => {
      els.studentSearchInput.value = '';
      els.studentClassroomFilter.value = '';
      els.studentAllergyOnly.checked = false;
      loadStudentsRoster();
    });
    els.btnToggleAddStudent?.addEventListener('click', () => {
      const showing = els.studentForm.style.display === 'block';
      if (showing) {
        els.studentForm.style.display = 'none';
      } else {
        resetStudentForm();
        els.studentForm.style.display = 'block';
      }
    });
    els.btnCancelStudentForm?.addEventListener('click', () => {
      resetStudentForm();
      els.studentForm.style.display = 'none';
    });
    els.studentForm?.addEventListener('submit', submitStudentForm);

    // LINE OA Simulator Controls
    els.btnLineSimSend?.addEventListener('click', () => {
      const val = els.lineSimTextInput.value;
      els.lineSimTextInput.value = '';
      sendLineSimText(val);
    });
    els.lineSimTextInput?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const val = els.lineSimTextInput.value;
        els.lineSimTextInput.value = '';
        sendLineSimText(val);
      }
    });
    els.btnLineSimPhoto?.addEventListener('click', () => els.lineSimFileInput.click());
    els.lineSimFileInput?.addEventListener('change', (e) => {
      if (e.target.files?.[0]) sendLineSimFile(e.target.files[0]);
      e.target.value = '';
    });
    document.querySelectorAll('.rich-menu-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.dataset.linePreset) {
          sendLineSimPreset(btn.dataset.linePreset);
        } else if (btn.dataset.lineCmd) {
          sendLineSimText(btn.dataset.lineCmd);
        }
      });
    });
  }

  async function init() {
    bindEvents();
    syncTechMode();
    await loadSystemStats();

    const urlParams = new URLSearchParams(window.location.search);
    const deepDocId = urlParams.get('doc');
    if (deepDocId) {
      await openDocumentById(deepDocId);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
