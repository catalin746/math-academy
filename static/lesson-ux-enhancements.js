(function(){
  const GRADE_ORDER = ['V', 'VI', 'VII', 'VIII'];
  const GRADE_LABELS = {
    V: 'clasa a V-a',
    VI: 'clasa a VI-a',
    VII: 'clasa a VII-a',
    VIII: 'clasa a VIII-a'
  };
  const DIFFICULTY_OPTIONS = [
    { value: 'individual', label: 'Nivelul lecției deschise' },
    { value: '1', label: 'Nivel 1 · Start' },
    { value: '2', label: 'Nivel 2 · Consolidare' },
    { value: '3', label: 'Nivel 3 · Avansat' },
    { value: '4', label: 'Nivel 4 · Concurs' },
    { value: '5', label: 'Nivel 5 · Olimpiadă' }
  ];
  const UX_STATE_KEY = window.mathStorageKey ? window.mathStorageKey('math-site-lesson-ux-v1') : 'math-site-lesson-ux-v1';

  const state = {
    session: null,
    prefs: null,
    completedLessons: new Set(),
    saveTimer: 0,
    refreshTimer: 0,
    lastVisibleLessonId: ''
  };

  function safeParse(raw, fallback){
    try{
      return raw ? JSON.parse(raw) : fallback;
    }catch(error){
      return fallback;
    }
  }

  function normalizeText(value){
    return String(value || '')
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .trim();
  }

  function slugify(value){
    return normalizeText(value).replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'all';
  }

  function requestSync(method, url, payload){
    try{
      const xhr = new XMLHttpRequest();
      xhr.open(method, url, false);
      xhr.withCredentials = true;
      xhr.setRequestHeader('Accept', 'application/json');
      if(payload != null){
        xhr.setRequestHeader('Content-Type', 'application/json');
      }
      xhr.send(payload == null ? null : JSON.stringify(payload));
      return {
        ok: xhr.status >= 200 && xhr.status < 300,
        status: xhr.status,
        data: safeParse(xhr.responseText, {})
      };
    }catch(error){
      return { ok: false, status: 0, data: null };
    }
  }

  function loadSession(){
    const response = requestSync('GET', '/api/session', null);
    if(response.ok && response.data){
      return response.data;
    }
    return { authenticated: false, user: null, snapshot: { lessonVisits: [] } };
  }

  function buildDefaultPrefs(){
    return {
      classFilters: {
        V: { chapter: 'all', difficulty: 'individual', query: '' },
        VI: { chapter: 'all', difficulty: 'individual', query: '' },
        VII: { chapter: 'all', difficulty: 'individual', query: '' },
        VIII: { chapter: 'all', difficulty: 'individual', query: '' }
      }
    };
  }

  function mergePrefs(value){
    const base = buildDefaultPrefs();
    const incoming = value && typeof value === 'object' ? value : {};
    GRADE_ORDER.forEach(function(grade){
      const nextValue = incoming.classFilters && incoming.classFilters[grade] ? incoming.classFilters[grade] : {};
      base.classFilters[grade] = {
        chapter: nextValue.chapter || 'all',
        difficulty: nextValue.difficulty || 'individual',
        query: typeof nextValue.query === 'string' ? nextValue.query : ''
      };
    });
    return base;
  }

  function loadPrefs(){
    const localValue = safeParse((function(){
      try{ return localStorage.getItem(UX_STATE_KEY); }catch(error){ return null; }
    })(), null);
    let sourceValue = localValue;
    if(state.session && state.session.authenticated){
      const remote = requestSync('GET', '/api/ui-state?key=' + encodeURIComponent(UX_STATE_KEY), null);
      if(remote.ok && remote.data && remote.data.value){
        sourceValue = remote.data.value;
      }
    }
    return mergePrefs(sourceValue);
  }

  function persistPrefs(){
    try{ localStorage.setItem(UX_STATE_KEY, JSON.stringify(state.prefs)); }catch(error){}
    if(!state.session || !state.session.authenticated) return;
    if(state.saveTimer){ window.clearTimeout(state.saveTimer); }
    state.saveTimer = window.setTimeout(function(){
      if(!window.fetch) return;
      fetch('/api/ui-state', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ key: UX_STATE_KEY, value: state.prefs })
      }).catch(function(){});
    }, 180);
  }

  function isStudent(){
    return !!(state.session && state.session.user && state.session.user.role === 'elev');
  }

  function buildCompletedLessons(){
    if(!isStudent()) return new Set();
    const visits = Array.isArray(state.session && state.session.snapshot && state.session.snapshot.lessonVisits)
      ? state.session.snapshot.lessonVisits
      : [];
    const userId = state.session.user.id;
    return new Set(
      visits
        .filter(function(item){ return item && item.lessonId && item.userId === userId; })
        .map(function(item){ return item.lessonId; })
    );
  }

  function classIdFromLesson(lessonId){
    const value = String(lessonId || '');
    if(value.indexOf('VIII-') === 0) return 'VIII';
    if(value.indexOf('VII-') === 0) return 'VII';
    if(value.indexOf('VI-') === 0) return 'VI';
    return 'V';
  }

  function getClassIdFromSidebar(sidebar){
    const workspace = sidebar ? sidebar.closest('.workspace') : null;
    return workspace ? workspace.id.replace(/^class/, '').replace(/View$/, '') : '';
  }

  function getClassPrefs(classId){
    if(!state.prefs.classFilters[classId]){
      state.prefs.classFilters[classId] = { chapter: 'all', difficulty: 'individual', query: '' };
    }
    return state.prefs.classFilters[classId];
  }

  function gradeLabel(classId){
    return GRADE_LABELS[classId] || ('clasa a ' + String(classId || '') + '-a');
  }

  function difficultyLabel(value){
    const option = DIFFICULTY_OPTIONS.find(function(item){ return item.value === String(value || 'individual'); });
    return option ? option.label : DIFFICULTY_OPTIONS[0].label;
  }

  function lessonTitle(lessonId){
    const page = document.querySelector('[data-lesson-page="' + String(lessonId || '').replace(/"/g, '\\"') + '"]');
    const heading = page ? page.querySelector('.banner-copy h2') : null;
    return heading ? heading.textContent.trim() : String(lessonId || '');
  }

  function activeVisibleLessonId(){
    const page = document.querySelector('.workspace.active .lesson-page.active');
    return page ? page.dataset.lessonPage || '' : '';
  }

  function decorateSidebar(sidebar){
    const classId = getClassIdFromSidebar(sidebar);
    if(!classId) return;
    sidebar.dataset.uxGrade = classId;

    sidebar.querySelectorAll('.sidebar-group').forEach(function(group){
      const heading = group.querySelector('h3');
      const title = heading ? heading.textContent.trim() : 'Capitol';
      group.dataset.uxChapter = slugify(title);
      group.dataset.uxChapterTitle = title;
      group.dataset.uxGrade = classId;
      group.querySelectorAll('.lesson-link').forEach(function(link){
        link.dataset.uxGrade = classId;
        link.dataset.uxChapter = group.dataset.uxChapter;
        const spans = link.querySelectorAll('span');
        const titleSpan = spans.length > 1 ? spans[1] : null;
        if(titleSpan){ titleSpan.classList.add('lesson-link-title'); }
        if(!link.querySelector('.lesson-ux-progress')){
          const badge = document.createElement('span');
          badge.className = 'lesson-ux-progress';
          badge.setAttribute('aria-hidden', 'true');
          badge.textContent = '○';
          link.appendChild(badge);
        }
      });
    });
  }

  function buildControls(sidebar){
    const tools = sidebar.querySelector('.sidebar-tools');
    const classId = getClassIdFromSidebar(sidebar);
    if(!tools || !classId) return;
    if(tools.querySelector('[data-lesson-ux-controls]')) return;

    const prefs = getClassPrefs(classId);
    const chapterOptions = Array.from(sidebar.querySelectorAll('.sidebar-group')).map(function(group){
      return {
        value: group.dataset.uxChapter,
        label: group.dataset.uxChapterTitle || 'Capitol'
      };
    });

    const wrapper = document.createElement('div');
    wrapper.className = 'lesson-ux-controls';
    wrapper.dataset.lessonUxControls = classId;
    wrapper.innerHTML = '' +
      '<div class="lesson-ux-filter-grid">' +
        '<label class="lesson-ux-field">' +
          '<span class="lesson-ux-label">Clasă</span>' +
          '<select class="lesson-ux-select" data-role="class-select">' +
            GRADE_ORDER.map(function(grade){
              return '<option value="' + grade + '"' + (grade === classId ? ' selected' : '') + '>' + gradeLabel(grade) + '</option>';
            }).join('') +
          '</select>' +
        '</label>' +
        '<label class="lesson-ux-field">' +
          '<span class="lesson-ux-label">Capitol</span>' +
          '<select class="lesson-ux-select" data-role="chapter-select">' +
            '<option value="all">Toate capitolele</option>' +
            chapterOptions.map(function(item){
              return '<option value="' + item.value + '">' + item.label + '</option>';
            }).join('') +
          '</select>' +
        '</label>' +
        '<label class="lesson-ux-field lesson-ux-field-wide">' +
          '<span class="lesson-ux-label">Dificultate exerciții</span>' +
          '<select class="lesson-ux-select" data-role="difficulty-select">' +
            DIFFICULTY_OPTIONS.map(function(item){
              return '<option value="' + item.value + '">' + item.label + '</option>';
            }).join('') +
          '</select>' +
          '<small class="lesson-ux-help">Nivelul ales se aplică automat lecțiilor pe care le deschizi în această clasă.</small>' +
        '</label>' +
      '</div>' +
      '<div class="lesson-ux-meta">' +
        '<span class="lesson-ux-pill" data-role="progress-pill"></span>' +
        '<span class="lesson-ux-pill" data-role="difficulty-pill"></span>' +
      '</div>' +
      '<div class="lesson-ux-resume" data-role="resume-card" hidden>' +
        '<strong>Continuă de unde ai rămas</strong>' +
        '<p data-role="resume-meta"></p>' +
        '<button class="lesson-ux-resume-btn" data-role="resume-button" type="button">Reia lecția</button>' +
      '</div>';
    tools.appendChild(wrapper);

    const classSelect = wrapper.querySelector('[data-role="class-select"]');
    const chapterSelect = wrapper.querySelector('[data-role="chapter-select"]');
    const difficultySelect = wrapper.querySelector('[data-role="difficulty-select"]');
    const resumeButton = wrapper.querySelector('[data-role="resume-button"]');
    const searchInput = sidebar.querySelector('.lesson-search');
    const clearButton = sidebar.querySelector('.search-clear');

    chapterSelect.value = chapterOptions.some(function(item){ return item.value === prefs.chapter; }) ? prefs.chapter : 'all';
    difficultySelect.value = prefs.difficulty || 'individual';

    classSelect.addEventListener('change', function(){
      const target = document.querySelector('.class-card[data-class="' + classSelect.value + '"]') || document.querySelector('[data-class="' + classSelect.value + '"]');
      if(target){ target.click(); }
      classSelect.value = classId;
    });

    chapterSelect.addEventListener('change', function(){
      getClassPrefs(classId).chapter = chapterSelect.value || 'all';
      persistPrefs();
      applyFilters(sidebar);
    });

    difficultySelect.addEventListener('change', function(){
      getClassPrefs(classId).difficulty = difficultySelect.value || 'individual';
      persistPrefs();
      applyDifficultyPreferenceToVisibleLesson(classId, true);
      updateSidebarMeta(sidebar);
    });

    if(searchInput){
      searchInput.addEventListener('input', function(){
        getClassPrefs(classId).query = searchInput.value || '';
        persistPrefs();
        applyFilters(sidebar);
      });
      searchInput.value = prefs.query || '';
      searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    }else{
      applyFilters(sidebar);
    }

    if(clearButton){
      clearButton.addEventListener('click', function(){
        window.requestAnimationFrame(function(){
          getClassPrefs(classId).query = '';
          persistPrefs();
          applyFilters(sidebar);
        });
      });
    }

    resumeButton.addEventListener('click', function(){
      const topResume = document.getElementById('resumeBtn');
      if(topResume && !topResume.hidden){
        topResume.click();
      }
    });

    updateSidebarMeta(sidebar);
    refreshResumeCards();
  }

  function updateCountLabel(sidebar, visible, total){
    const countLabel = sidebar.querySelector('.sidebar-count');
    if(!countLabel) return;
    countLabel.textContent = visible === total ? (total + ' lecții') : (visible + ' din ' + total + ' lecții');
  }

  function applyFilters(sidebar){
    const classId = getClassIdFromSidebar(sidebar);
    const prefs = getClassPrefs(classId);
    const searchInput = sidebar.querySelector('.lesson-search');
    const query = normalizeText(searchInput ? searchInput.value : prefs.query);
    const chapter = prefs.chapter || 'all';
    const groups = Array.from(sidebar.querySelectorAll('.sidebar-group'));
    const allLinks = Array.from(sidebar.querySelectorAll('.lesson-link'));
    let visibleLessons = 0;

    groups.forEach(function(group){
      const groupTitle = normalizeText(group.dataset.uxChapterTitle || '');
      let groupVisible = false;
      group.querySelectorAll('.lesson-link').forEach(function(link){
        const text = normalizeText(link.textContent);
        const matchesQuery = !query || text.indexOf(query) !== -1 || groupTitle.indexOf(query) !== -1;
        const matchesChapter = chapter === 'all' || group.dataset.uxChapter === chapter;
        const visible = matchesQuery && matchesChapter;
        link.classList.toggle('hidden-by-ux-filter', !visible);
        if(visible){
          groupVisible = true;
          visibleLessons += 1;
        }
      });
      group.classList.toggle('group-hidden-ux', !groupVisible);
    });

    updateCountLabel(sidebar, visibleLessons, allLinks.length);
    updateSidebarMeta(sidebar);
  }

  function updateSidebarMeta(sidebar){
    const classId = getClassIdFromSidebar(sidebar);
    const wrapper = sidebar.querySelector('[data-lesson-ux-controls]');
    if(!wrapper || !classId) return;
    const prefs = getClassPrefs(classId);
    const totalLessons = sidebar.querySelectorAll('.lesson-link').length;
    const completedLessons = Array.from(sidebar.querySelectorAll('.lesson-link')).filter(function(link){
      return state.completedLessons.has(link.dataset.lesson);
    }).length;
    const progressPill = wrapper.querySelector('[data-role="progress-pill"]');
    const difficultyPill = wrapper.querySelector('[data-role="difficulty-pill"]');

    if(progressPill){
      if(isStudent()){
        progressPill.hidden = false;
        progressPill.classList.toggle('completed', completedLessons > 0);
        progressPill.textContent = 'Progres: ' + completedLessons + '/' + totalLessons + ' lecții';
      }else{
        progressPill.hidden = false;
        progressPill.classList.remove('completed');
        progressPill.textContent = 'Bifele apar pe contul de elev';
      }
    }

    if(difficultyPill){
      difficultyPill.hidden = false;
      difficultyPill.classList.toggle('completed', prefs.difficulty !== 'individual');
      difficultyPill.textContent = 'Exerciții: ' + difficultyLabel(prefs.difficulty);
    }
  }

  function updateCompletionUi(){
    const student = isStudent();
    document.querySelectorAll('.lesson-link').forEach(function(link){
      const completed = student && state.completedLessons.has(link.dataset.lesson);
      link.classList.toggle('is-completed', completed);
      const badge = link.querySelector('.lesson-ux-progress');
      if(badge){
        badge.textContent = completed ? '✓' : '○';
        badge.title = completed ? 'Lecție parcursă' : 'Lecție nouă';
      }
    });

    document.querySelectorAll('[data-lesson-page]').forEach(function(page){
      const lessonId = page.dataset.lessonPage;
      const meta = page.querySelector('.lesson-toolbar-meta');
      if(!meta) return;
      let badge = meta.querySelector('[data-lesson-complete-status]');
      if(!badge){
        badge = document.createElement('span');
        badge.className = 'lesson-status lesson-ux-toolbar-status';
        badge.dataset.lessonCompleteStatus = lessonId;
        meta.appendChild(badge);
      }
      if(!student){
        badge.hidden = true;
        return;
      }
      badge.hidden = false;
      const completed = state.completedLessons.has(lessonId);
      badge.classList.toggle('is-completed', completed);
      badge.textContent = completed ? '✓ Lecție parcursă' : '◦ Lecție nouă';
    });

    document.querySelectorAll('.sidebar').forEach(updateSidebarMeta);
  }

  function refreshResumeCards(){
    const resumeBtn = document.getElementById('resumeBtn');
    const classId = resumeBtn ? resumeBtn.dataset.classId || '' : '';
    const lessonId = resumeBtn ? resumeBtn.dataset.lessonId || '' : '';
    const ready = !!(resumeBtn && !resumeBtn.hidden && classId && lessonId);
    const title = ready ? lessonTitle(lessonId) : '';

    if(resumeBtn && ready){
      resumeBtn.title = 'Continuă de unde ai rămas: ' + title;
    }

    document.querySelectorAll('[data-role="resume-card"]').forEach(function(card){
      const meta = card.querySelector('[data-role="resume-meta"]');
      if(!ready){
        card.hidden = true;
        if(meta){ meta.textContent = ''; }
        return;
      }
      card.hidden = false;
      if(meta){
        meta.textContent = gradeLabel(classId) + ' · ' + title;
      }
    });
  }

  function markLessonCompletedIfNeeded(lessonId){
    if(!isStudent() || !lessonId) return;
    state.completedLessons.add(lessonId);
  }

  function applyDifficultyPreferenceToVisibleLesson(classId, forceRefresh){
    const visibleLessonId = activeVisibleLessonId();
    if(!visibleLessonId || classIdFromLesson(visibleLessonId) !== classId) return;
    const difficulty = getClassPrefs(classId).difficulty;
    if(!difficulty || difficulty === 'individual') return;
    const page = document.querySelector('.workspace.active .lesson-page.active');
    const select = page ? page.querySelector('[data-role="difficulty"]') : null;
    if(!select) return;
    if(forceRefresh || select.value !== String(difficulty)){
      select.value = String(difficulty);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  function refreshEnhancements(lessonIdHint){
    const visibleLessonId = activeVisibleLessonId();
    if(visibleLessonId){
      markLessonCompletedIfNeeded(visibleLessonId);
      if(visibleLessonId !== state.lastVisibleLessonId || lessonIdHint){
        applyDifficultyPreferenceToVisibleLesson(classIdFromLesson(visibleLessonId), false);
      }
      state.lastVisibleLessonId = visibleLessonId;
    }else{
      state.lastVisibleLessonId = '';
    }
    updateCompletionUi();
    refreshResumeCards();
    document.querySelectorAll('.sidebar').forEach(function(sidebar){
      applyFilters(sidebar);
    });
  }

  function queueRefresh(lessonIdHint){
    if(state.refreshTimer){ window.clearTimeout(state.refreshTimer); }
    state.refreshTimer = window.setTimeout(function(){
      refreshEnhancements(lessonIdHint || '');
    }, 40);
  }

  function wrapLessonTracking(){
    if(!window.MathAcademyApp || typeof window.MathAcademyApp.onLessonOpened !== 'function') return;
    const original = window.MathAcademyApp.onLessonOpened;
    if(original.__uxWrapped) return;
    const wrapped = function(lessonId, scroll){
      const result = original.apply(this, arguments);
      queueRefresh(lessonId);
      return result;
    };
    wrapped.__uxWrapped = true;
    window.MathAcademyApp.onLessonOpened = wrapped;
  }

  function bindExtraTriggers(){
    const homeBtn = document.getElementById('homeBtn');
    const resumeBtn = document.getElementById('resumeBtn');
    const goVBtn = document.getElementById('goVBtn');
    [homeBtn, resumeBtn, goVBtn].forEach(function(button){
      if(!button) return;
      button.addEventListener('click', function(){ queueRefresh(''); });
    });
    document.querySelectorAll('[data-class], .lesson-nav-btn, .lesson-link').forEach(function(node){
      node.addEventListener('click', function(){ queueRefresh(''); });
    });
  }

  function init(){
    state.session = loadSession();
    state.prefs = loadPrefs();
    state.completedLessons = buildCompletedLessons();

    document.querySelectorAll('.sidebar').forEach(function(sidebar){
      decorateSidebar(sidebar);
      buildControls(sidebar);
    });

    wrapLessonTracking();
    bindExtraTriggers();
    refreshEnhancements('');
  }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', init, { once: true });
  }else{
    init();
  }
})();
