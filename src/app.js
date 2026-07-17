(() => {
  "use strict";

  const data = window.PAPERS_DATA || { papers: [] };
  const papers = Array.isArray(data.papers) ? data.papers : [];
  const $ = (selector) => document.querySelector(selector);
  const list = $("#paper-list");
  const template = $("#paper-template");
  const searchInput = $("#search-input");
  const periodSelect = $("#period-select");
  const categorySelect = $("#category-select");
  const sortSelect = $("#sort-select");
  const emptyState = $("#empty-state");

  const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  const compactDateFormatter = new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  });

  function validDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatDate(value, compact = false) {
    const date = validDate(value);
    if (!date) return "未知日期";
    return (compact ? compactDateFormatter : dateFormatter).format(date);
  }

  function initializeHeader() {
    const now = new Date();
    $("#edition-date").textContent = dateFormatter.format(now);
    $("#total-stat").textContent = String(papers.length);
    $("#new-stat").textContent = String(Number(data.new_count) || 0);
    const newest = papers.map((paper) => validDate(paper.published)).filter(Boolean).sort((a, b) => b - a)[0];
    $("#latest-stat").textContent = newest ? compactDateFormatter.format(newest) : "—";

    const checked = validDate(data.last_checked);
    const success = validDate(data.last_success);
    $("#edition-status").textContent = data.fetch_error
      ? `上次成功：${success ? dateFormatter.format(success) : "暂无"}`
      : checked
        ? `已于 ${checked.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} 检索`
        : "等待首次检索";
    $("#last-updated").textContent = checked
      ? `最后检查：${checked.toLocaleString("zh-CN")}`
      : "尚未更新";

    if (data.fetch_error) {
      const notice = $("#notice");
      notice.hidden = false;
      notice.textContent = "本次 arXiv 检索未成功，当前展示最近一次缓存的数据。";
    }
  }

  function initializeCategories() {
    const categories = [...new Set(papers.flatMap((paper) => paper.categories || []))].sort();
    categories.forEach((category) => {
      const option = document.createElement("option");
      option.value = category;
      option.textContent = category;
      categorySelect.append(option);
    });
  }

  function matchesFilters(paper) {
    const needle = searchInput.value.trim().toLocaleLowerCase();
    const haystack = [paper.title, paper.abstract, ...(paper.authors || []), ...(paper.categories || [])]
      .join(" ")
      .toLocaleLowerCase();
    if (needle && !haystack.includes(needle)) return false;

    if (categorySelect.value !== "all" && !(paper.categories || []).includes(categorySelect.value)) {
      return false;
    }

    if (periodSelect.value !== "all") {
      const published = validDate(paper.published);
      if (!published) return false;
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - Number(periodSelect.value));
      if (published < cutoff) return false;
    }
    return true;
  }

  function sortedPapers(filtered) {
    const mode = sortSelect.value;
    return [...filtered].sort((left, right) => {
      if (mode === "title") return String(left.title).localeCompare(String(right.title), "en");
      const leftValue = validDate(left[mode])?.getTime() || 0;
      const rightValue = validDate(right[mode])?.getTime() || 0;
      return rightValue - leftValue;
    });
  }

  function renderPaper(paper, index) {
    const fragment = template.content.cloneNode(true);
    const card = fragment.querySelector(".paper-card");
    fragment.querySelector(".paper-index").textContent = String(index + 1).padStart(2, "0");

    const date = fragment.querySelector(".paper-date");
    date.textContent = `PUBLISHED · ${formatDate(paper.published)}`;
    date.dateTime = paper.published || "";

    const categoryContainer = fragment.querySelector(".paper-categories");
    (paper.categories || []).slice(0, 4).forEach((category) => {
      const tag = document.createElement("span");
      tag.className = "category-tag";
      tag.textContent = category;
      categoryContainer.append(tag);
    });

    const title = fragment.querySelector(".paper-title");
    title.textContent = paper.title || "未命名论文";
    title.href = paper.arxiv_url || "#";
    fragment.querySelector(".paper-authors").textContent = (paper.authors || []).join(" · ") || "作者未知";

    const abstract = fragment.querySelector(".paper-abstract");
    abstract.textContent = paper.abstract || "暂无摘要。";
    const toggle = fragment.querySelector(".abstract-toggle");
    toggle.addEventListener("click", () => {
      const expanded = abstract.classList.toggle("expanded");
      toggle.setAttribute("aria-expanded", String(expanded));
      toggle.textContent = expanded ? "收起摘要" : "展开摘要";
    });

    const pageLink = fragment.querySelector(".paper-page");
    pageLink.href = paper.arxiv_url || "#";
    pageLink.setAttribute("aria-label", `在 arXiv 查看：${paper.title}`);
    const pdfLink = fragment.querySelector(".paper-pdf");
    pdfLink.href = paper.pdf_url || paper.arxiv_url || "#";
    pdfLink.setAttribute("aria-label", `打开 PDF：${paper.title}`);
    fragment.querySelector(".arxiv-id").textContent = `arXiv:${paper.versioned_id || paper.id || "—"}`;
    card.dataset.paperId = paper.id || "";
    return fragment;
  }

  function render() {
    const visible = sortedPapers(papers.filter(matchesFilters));
    list.replaceChildren(...visible.map(renderPaper));
    $("#result-count").textContent = `${visible.length} 篇论文`;
    emptyState.hidden = visible.length !== 0;
  }

  [searchInput, periodSelect, categorySelect, sortSelect].forEach((control) => {
    control.addEventListener(control === searchInput ? "input" : "change", render);
  });
  $("#clear-filters").addEventListener("click", () => {
    searchInput.value = "";
    periodSelect.value = "all";
    categorySelect.value = "all";
    sortSelect.value = "published";
    render();
    searchInput.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement !== searchInput) {
      event.preventDefault();
      searchInput.focus();
    }
    if (event.key === "Escape" && document.activeElement === searchInput) {
      searchInput.value = "";
      render();
      searchInput.blur();
    }
  });

  initializeHeader();
  initializeCategories();
  render();
})();
