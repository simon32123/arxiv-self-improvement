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
  const pagination = $("#pagination");
  const previousPage = $("#previous-page");
  const nextPage = $("#next-page");
  const pageNumbers = $("#page-numbers");
  const pageStatus = $("#page-status");
  const resultsHead = $(".results-head");
  const PAGE_SIZE = 30;
  let currentPage = 1;

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
      if (mode === "relevance") {
        const scoreDifference = Number(right.relevance_score || 0) - Number(left.relevance_score || 0);
        if (scoreDifference) return scoreDifference;
        return (validDate(right.published)?.getTime() || 0) - (validDate(left.published)?.getTime() || 0);
      }
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

    const relevance = fragment.querySelector(".relevance-score");
    const score = Math.max(0, Math.min(100, Math.round(Number(paper.relevance_score) || 0)));
    const reasons = Array.isArray(paper.relevance_reasons) ? paper.relevance_reasons : [];
    relevance.querySelector("strong").textContent = String(score);
    relevance.classList.add(score >= 80 ? "high" : score >= 60 ? "medium" : "standard");
    relevance.title = reasons.length ? reasons.join("；") : "根据标题、摘要与分类计算";
    relevance.setAttribute("aria-label", `Agent self-improvement 相关度 ${score} 分`);

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

  function paginationItems(current, total) {
    if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);

    const pages = new Set([1, total, current - 1, current, current + 1]);
    if (current <= 3) [2, 3, 4].forEach((page) => pages.add(page));
    if (current >= total - 2) [total - 3, total - 2, total - 1].forEach((page) => pages.add(page));

    const ordered = [...pages].filter((page) => page > 0 && page <= total).sort((a, b) => a - b);
    const items = [];
    ordered.forEach((page, index) => {
      if (index && page - ordered[index - 1] > 1) items.push("ellipsis");
      items.push(page);
    });
    return items;
  }

  function renderPagination(totalPages) {
    pagination.hidden = totalPages <= 1;
    if (totalPages <= 1) {
      pageNumbers.replaceChildren();
      pageStatus.textContent = "";
      return;
    }

    previousPage.disabled = currentPage === 1;
    nextPage.disabled = currentPage === totalPages;
    pageStatus.textContent = `第 ${currentPage} / ${totalPages} 页`;

    const controls = paginationItems(currentPage, totalPages).map((item) => {
      if (item === "ellipsis") {
        const ellipsis = document.createElement("span");
        ellipsis.className = "pagination-ellipsis";
        ellipsis.textContent = "…";
        ellipsis.setAttribute("aria-hidden", "true");
        return ellipsis;
      }

      const button = document.createElement("button");
      button.type = "button";
      button.className = "page-number";
      button.textContent = String(item);
      button.setAttribute("aria-label", `第 ${item} 页`);
      if (item === currentPage) {
        button.classList.add("active");
        button.setAttribute("aria-current", "page");
      }
      button.addEventListener("click", () => changePage(item));
      return button;
    });
    pageNumbers.replaceChildren(...controls);
  }

  function render() {
    const filtered = sortedPapers(papers.filter(matchesFilters));
    const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    currentPage = Math.min(currentPage, totalPages);
    const start = (currentPage - 1) * PAGE_SIZE;
    const visible = filtered.slice(start, start + PAGE_SIZE);
    list.replaceChildren(...visible.map((paper, index) => renderPaper(paper, start + index)));
    $("#result-count").textContent = filtered.length
      ? `${filtered.length} 篇论文 · 第 ${currentPage} / ${totalPages} 页`
      : "0 篇论文";
    emptyState.hidden = filtered.length !== 0;
    renderPagination(filtered.length ? totalPages : 0);
  }

  function changePage(page) {
    if (page === currentPage) return;
    currentPage = page;
    render();
    resultsHead.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
  }

  function resetPageAndRender() {
    currentPage = 1;
    render();
  }

  [searchInput, periodSelect, categorySelect, sortSelect].forEach((control) => {
    control.addEventListener(control === searchInput ? "input" : "change", resetPageAndRender);
  });
  previousPage.addEventListener("click", () => changePage(currentPage - 1));
  nextPage.addEventListener("click", () => changePage(currentPage + 1));
  $("#clear-filters").addEventListener("click", () => {
    searchInput.value = "";
    periodSelect.value = "all";
    categorySelect.value = "all";
    sortSelect.value = "published";
    resetPageAndRender();
    searchInput.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement !== searchInput) {
      event.preventDefault();
      searchInput.focus();
    }
    if (event.key === "Escape" && document.activeElement === searchInput) {
      searchInput.value = "";
      resetPageAndRender();
      searchInput.blur();
    }
  });

  initializeHeader();
  initializeCategories();
  render();
})();
