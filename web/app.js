'use strict';

(function () {
  var tg = (window.Telegram && window.Telegram.WebApp) ? window.Telegram.WebApp : null;

  var state = {
    screen: null,
    statuses: [],
    history: [],
    laptop: null,
    mode: 'create',
    qr: null,
    scanSource: null,
    reader: null,
    stream: null,
    scanTimer: null,
    zxingPromise: null,
    searchTimer: null,
    filter: { by: 'all', status: '', sort: 'new' },
    items: [],
    total: 0,
    counts: {},
    listRequest: 0
  };

  var PAGE_SIZE = 50;

  var mainHandler = null;
  var backHandler = null;

  var FIELD_NAMES = [
    'serial_number', 'manufacturer', 'model', 'inventory_number',
    'status', 'assigned_to', 'location', 'notes'
  ];

  var SOURCE_LABELS = {
    qr: 'сканирование QR',
    barcode: 'сканирование штрих-кода',
    manual: 'ручной ввод'
  };

  // Карточка: секции и строки. stack — длинное значение под подписью, а не справа.
  var CARD_SECTIONS = [
    ['Закрепление', [
      ['assigned_to', 'Сотрудник'],
      ['location', 'Расположение']
    ]],
    ['Устройство', [
      ['manufacturer', 'Производитель'],
      ['model', 'Модель'],
      ['serial_number', 'Серийный номер'],
      ['inventory_number', 'Инвентарный номер']
    ]],
    ['Примечание', [
      ['notes', 'Комментарий', 'stack']
    ]],
    ['История', [
      ['source_label', 'Источник'],
      ['created_at_label', 'Добавлен'],
      ['updated_at_label', 'Изменён'],
      ['qr_raw', 'Содержимое кода', 'stack']
    ]]
  ];

  var SEARCH_PLACEHOLDERS = {
    all: 'Серийник, сотрудник, модель…',
    serial: 'Серийный номер',
    employee: 'Фамилия или имя сотрудника'
  };

  var SORT_LABELS = {
    'new': 'Сначала новые',
    status: 'По статусу'
  };

  // Иконки — статичная разметка, в неё никогда не подставляются данные.
  var ICONS = {
    laptop: '<svg viewBox="0 0 24 24" width="22" height="22"><rect x="4" y="5" width="16" height="11" rx="2"/><path d="M2 19h20"/></svg>',
    person: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
    pin: '<svg viewBox="0 0 24 24"><path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>',
    empty: '<svg viewBox="0 0 24 24" width="48" height="48"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5M8.5 11h5"/></svg>'
  };

  function el(id) {
    return document.getElementById(id);
  }

  // --- тема ---------------------------------------------------------------

  var THEME_MAP = {
    bg_color: '--bg',
    text_color: '--text',
    hint_color: '--hint',
    link_color: '--link',
    button_color: '--button',
    button_text_color: '--button-text',
    secondary_bg_color: '--secondary-bg',
    section_bg_color: '--section-bg',
    destructive_text_color: '--destructive'
  };

  function hexToRgba(hex, alpha) {
    var match = /^#?([0-9a-f]{6})$/i.exec(String(hex || '').trim());
    if (!match) return null;
    var value = parseInt(match[1], 16);
    return 'rgba(' + ((value >> 16) & 255) + ', ' + ((value >> 8) & 255) + ', ' +
      (value & 255) + ', ' + alpha + ')';
  }

  function applyTheme() {
    if (!tg || !tg.themeParams) return;
    var root = document.documentElement;
    var params = tg.themeParams;
    if (tg.colorScheme) root.setAttribute('data-scheme', tg.colorScheme);
    Object.keys(THEME_MAP).forEach(function (key) {
      if (params[key]) {
        root.style.setProperty(THEME_MAP[key], params[key]);
      }
    });
    // Старые клиенты не присылают section_bg — тогда секции цвета основного фона.
    if (!params.section_bg_color && params.bg_color) {
      root.style.setProperty('--section-bg', params.bg_color);
    }
    var soft = hexToRgba(params.button_color, 0.12);
    if (soft) root.style.setProperty('--accent-soft', soft);

    // Шапка и фон Telegram под цвет страницы, чтобы не было полосы другого цвета.
    if (typeof tg.isVersionAtLeast === 'function' && tg.isVersionAtLeast('6.1')) {
      try {
        tg.setHeaderColor('secondary_bg_color');
        tg.setBackgroundColor('secondary_bg_color');
      } catch (e) { /* клиент не умеет — не страшно */ }
    }
  }

  // --- кнопки Telegram ----------------------------------------------------

  function setMainButton(text, handler) {
    if (!tg || !tg.MainButton) return;
    if (mainHandler) {
      tg.MainButton.offClick(mainHandler);
      mainHandler = null;
    }
    if (!handler) {
      tg.MainButton.hide();
      return;
    }
    mainHandler = handler;
    tg.MainButton.setText(text);
    tg.MainButton.onClick(handler);
    tg.MainButton.show();
  }

  function setBackButton(handler) {
    if (!tg || !tg.BackButton) return;
    if (backHandler) {
      tg.BackButton.offClick(backHandler);
      backHandler = null;
    }
    if (!handler) {
      tg.BackButton.hide();
      return;
    }
    backHandler = handler;
    tg.BackButton.onClick(handler);
    tg.BackButton.show();
  }

  function alertMessage(text) {
    if (tg && typeof tg.showAlert === 'function') {
      tg.showAlert(text);
    } else {
      window.alert(text);
    }
  }

  // --- API ----------------------------------------------------------------

  function api(path, options) {
    options = options || {};
    var headers = { 'X-Telegram-Init-Data': tg ? (tg.initData || '') : '' };
    if (options.body) headers['Content-Type'] = 'application/json';

    return fetch(path, {
      method: options.method || 'GET',
      headers: headers,
      body: options.body ? JSON.stringify(options.body) : undefined
    }).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (e) { data = null; }
        }
        if (!response.ok) {
          var error = new Error((data && data.message) || ('Ошибка ' + response.status));
          error.status = response.status;
          error.data = data;
          throw error;
        }
        return data;
      });
    });
  }

  function handleError(error) {
    if (error && error.status === 403) {
      showDenied(error.data);
      return;
    }
    if (error && error.status === 401) {
      showError((error.data && error.data.message) || 'Не удалось подтвердить вход. Переоткрой приложение из Telegram.');
      return;
    }
    alertMessage((error && error.message) || 'Непредвиденная ошибка');
  }

  // --- экраны -------------------------------------------------------------

  function showScreen(name) {
    // Уходим со сканера — камеру отпускаем в любом случае, каким бы путём ни ушли.
    if (name !== 'scan') stopScanner();
    ['loading', 'denied', 'error', 'list', 'scan', 'form', 'card'].forEach(function (screen) {
      el('screen-' + screen).hidden = (screen !== name);
    });
    state.screen = name;
    window.scrollTo(0, 0);
  }

  function goBack() {
    var previous = state.history.pop();
    if (!previous) {
      openList();
      return;
    }
    if (previous.screen === 'list') {
      openList(true);
    } else if (previous.screen === 'card' && previous.laptop) {
      openCard(previous.laptop, null, true);
    } else {
      openList(true);
    }
  }

  function pushHistory() {
    if (state.screen === 'list' || state.screen === 'card') {
      state.history.push({ screen: state.screen, laptop: state.laptop });
    }
  }

  function showDenied(data) {
    el('denied-id').textContent = (data && data.telegram_id) ? String(data.telegram_id) : '—';
    el('denied-hint').textContent = (data && data.message) ? data.message : '';
    setMainButton(null, null);
    setBackButton(null);
    showScreen('denied');
  }

  function showError(message) {
    el('error-text').textContent = message;
    setMainButton(null, null);
    setBackButton(null);
    showScreen('error');
  }

  // --- список -------------------------------------------------------------

  function formatDate(value) {
    if (!value) return '';
    var date = new Date(String(value).replace(' ', 'T') + 'Z');
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString('ru-RU', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  }

  function plural(n, forms) {
    var mod10 = n % 10;
    var mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return forms[0];
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
    return forms[2];
  }

  function countLaptops(n) {
    return n + ' ' + plural(n, ['ноутбук', 'ноутбука', 'ноутбуков']);
  }

  function searchQuery() {
    return el('search').value.trim();
  }

  // Совпадает с search_key на сервере: регистр и «ё» не важны.
  function foldCase(text) {
    return String(text).toLowerCase().replace(/ё/g, 'е');
  }

  // Подсвечивает найденный фрагмент. Только узлы DOM — без innerHTML с данными.
  function appendHighlighted(parent, text, query) {
    text = String(text);
    var index = -1;
    if (query) {
      var haystack = foldCase(text);
      // toLowerCase иногда меняет длину строки — тогда позиции не совпадут.
      if (haystack.length === text.length) index = haystack.indexOf(foldCase(query));
    }
    if (index === -1) {
      parent.appendChild(document.createTextNode(text));
      return;
    }
    var mark = document.createElement('mark');
    mark.textContent = text.slice(index, index + query.length);
    parent.appendChild(document.createTextNode(text.slice(0, index)));
    parent.appendChild(mark);
    parent.appendChild(document.createTextNode(text.slice(index + query.length)));
  }

  function makePill(code, label) {
    var pill = document.createElement('span');
    pill.className = 'pill';
    pill.setAttribute('data-status', code);
    pill.textContent = label;
    return pill;
  }

  function metaItem(icon, text, query, extraClass) {
    var span = document.createElement('span');
    span.className = 'meta' + (extraClass ? ' ' + extraClass : '');
    span.innerHTML = ICONS[icon];
    // Текст в отдельном span: иначе куски вокруг <mark> станут разными
    // flex-элементами и между ними появится gap.
    var label = document.createElement('span');
    appendHighlighted(label, text, query);
    span.appendChild(label);
    return span;
  }

  function renderItem(laptop, query) {
    var by = state.filter.by;
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'item';
    button.setAttribute('data-status', laptop.status);

    var icon = document.createElement('span');
    icon.className = 'item-icon';
    icon.innerHTML = ICONS.laptop;

    var body = document.createElement('span');
    body.className = 'item-body';

    var top = document.createElement('span');
    top.className = 'item-top';
    var title = document.createElement('span');
    title.className = 'item-title';
    var name = [laptop.manufacturer, laptop.model].filter(Boolean).join(' ');
    appendHighlighted(title, name || 'Без модели', by === 'all' ? query : '');
    top.appendChild(title);
    top.appendChild(makePill(laptop.status, laptop.status_label));

    var serial = document.createElement('span');
    serial.className = 'item-serial';
    serial.style.display = 'block';
    appendHighlighted(serial, laptop.serial_number, by !== 'employee' ? query : '');
    if (laptop.inventory_number) {
      var inv = document.createElement('span');
      inv.className = 'inv';
      inv.appendChild(document.createTextNode(' · инв. '));
      appendHighlighted(inv, laptop.inventory_number, by === 'all' ? query : '');
      serial.appendChild(inv);
    }

    body.appendChild(top);
    body.appendChild(serial);

    if (laptop.assigned_to || laptop.location) {
      var meta = document.createElement('span');
      meta.className = 'item-meta';
      if (laptop.assigned_to) {
        meta.appendChild(metaItem('person', laptop.assigned_to,
          by !== 'serial' ? query : '', 'meta--person'));
      }
      if (laptop.location) meta.appendChild(metaItem('pin', laptop.location, ''));
      body.appendChild(meta);
    }

    button.appendChild(icon);
    button.appendChild(body);
    button.addEventListener('click', function () {
      openCard(laptop);
    });
    return button;
  }

  function renderEmpty(container) {
    var filtered = searchQuery() || state.filter.status;
    var box = document.createElement('div');
    box.className = 'empty';
    box.innerHTML = ICONS.empty;
    var head = document.createElement('b');
    head.textContent = filtered ? 'Ничего не найдено' : 'Пока пусто';
    var text = document.createElement('span');
    text.textContent = filtered
      ? 'Попробуй изменить запрос или выбрать другой статус.'
      : 'Отсканируй код на ноутбуке или добавь его вручную.';
    box.appendChild(head);
    box.appendChild(text);
    container.appendChild(box);
  }

  function renderList() {
    var container = el('list');
    container.textContent = '';
    var query = searchQuery();

    el('list-total').textContent = (query || state.filter.status)
      ? 'Найдено: ' + state.total
      : countLaptops(state.total);
    el('btn-more').hidden = state.items.length >= state.total;

    if (!state.items.length) {
      renderEmpty(container);
      return;
    }

    // Без фильтра по статусу сортировка «по статусу» показывает группы с заголовками.
    var grouped = state.filter.sort === 'status' && !state.filter.status;
    var currentGroup = null;
    state.items.forEach(function (laptop) {
      if (grouped && laptop.status !== currentGroup) {
        currentGroup = laptop.status;
        var header = document.createElement('div');
        header.className = 'group-title';
        header.setAttribute('data-status', laptop.status);
        var dot = document.createElement('span');
        dot.className = 'dot';
        header.appendChild(dot);
        header.appendChild(document.createTextNode(
          laptop.status_label + ' · ' + (state.counts[laptop.status] || 0)));
        container.appendChild(header);
      }
      container.appendChild(renderItem(laptop, query));
    });
  }

  function renderChips() {
    var container = el('status-chips');
    container.textContent = '';
    var all = 0;
    state.statuses.forEach(function (status) { all += state.counts[status.value] || 0; });

    var options = [{ value: '', label: 'Все', count: all }].concat(
      state.statuses.map(function (status) {
        return { value: status.value, label: status.label, count: state.counts[status.value] || 0 };
      })
    );

    options.forEach(function (option) {
      var active = state.filter.status === option.value;
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip' + (active ? ' active' : '');
      chip.setAttribute('role', 'radio');
      chip.setAttribute('aria-checked', active ? 'true' : 'false');
      if (option.value) {
        chip.setAttribute('data-status', option.value);
        var dot = document.createElement('span');
        dot.className = 'dot';
        chip.appendChild(dot);
      }
      chip.appendChild(document.createTextNode(option.label));
      var count = document.createElement('span');
      count.className = 'count';
      count.textContent = String(option.count);
      chip.appendChild(count);
      chip.addEventListener('click', function () {
        if (state.filter.status === option.value) return;
        state.filter.status = option.value;
        loadList();
      });
      container.appendChild(chip);
    });
  }

  // Шапка: общее количество и полоса распределения. Обновляется только без
  // поиска — иначе показала бы не всю базу, а результаты поиска.
  function renderSummary() {
    var total = 0;
    state.statuses.forEach(function (status) { total += state.counts[status.value] || 0; });
    el('list-summary').textContent = total ? 'Всего ' + countLaptops(total) : 'База пока пустая';

    var bar = el('dist-bar');
    bar.textContent = '';
    bar.hidden = !total;
    state.statuses.forEach(function (status) {
      var n = state.counts[status.value] || 0;
      if (!n) return;
      var part = document.createElement('span');
      part.setAttribute('data-status', status.value);
      part.style.flex = n + ' 1 0';
      part.title = status.label + ': ' + n;
      bar.appendChild(part);
    });
  }

  function listUrl(offset) {
    var params = [
      'limit=' + PAGE_SIZE,
      'offset=' + offset,
      'q=' + encodeURIComponent(searchQuery()),
      'by=' + state.filter.by,
      'sort=' + state.filter.sort
    ];
    if (state.filter.status) params.push('status=' + state.filter.status);
    return '/api/laptops?' + params.join('&');
  }

  function loadList(append) {
    var offset = append ? state.items.length : 0;
    // Ответы на устаревшие запросы (пользователь печатает дальше) отбрасываем.
    var request = ++state.listRequest;
    el('btn-more').disabled = true;
    return api(listUrl(offset))
      .then(function (data) {
        if (request !== state.listRequest) return;
        state.items = append ? state.items.concat(data.items) : data.items;
        state.total = data.total;
        state.counts = data.status_counts || {};
        if (!searchQuery()) renderSummary();
        renderChips();
        renderList();
      })
      .catch(handleError)
      .then(function () {
        el('btn-more').disabled = false;
      });
  }

  function openList(skipHistory) {
    if (!skipHistory) state.history = [];
    state.laptop = null;
    setMainButton(null, null);
    setBackButton(null);
    showScreen('list');
    return loadList();
  }

  function setSearchBy(by) {
    state.filter.by = by;
    Array.prototype.forEach.call(el('search-by').children, function (button) {
      var active = button.getAttribute('data-by') === by;
      button.classList.toggle('active', active);
      button.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    el('search').placeholder = SEARCH_PLACEHOLDERS[by];
    if (searchQuery()) loadList();
  }

  function toggleSort() {
    state.filter.sort = state.filter.sort === 'new' ? 'status' : 'new';
    el('sort-label').textContent = SORT_LABELS[state.filter.sort];
    loadList();
  }

  // --- форма --------------------------------------------------------------

  function fillStatusOptions() {
    var picker = el('form-status');
    picker.textContent = '';
    state.statuses.forEach(function (status) {
      var option = document.createElement('label');
      option.className = 'status-option';
      option.setAttribute('data-status', status.value);
      var input = document.createElement('input');
      input.type = 'radio';
      input.name = 'status';
      input.value = status.value;
      var text = document.createElement('span');
      text.textContent = status.label;
      option.appendChild(input);
      option.appendChild(text);
      picker.appendChild(option);
    });
  }

  function markInvalid(input, invalid) {
    if (!input || !input.classList) return;
    input.classList.toggle('invalid', invalid);
    var field = input.closest ? input.closest('.field') : null;
    if (field) field.classList.toggle('invalid', invalid);
  }

  function fillDatalist(id, values) {
    var list = el(id);
    list.textContent = '';
    (values || []).forEach(function (value) {
      var option = document.createElement('option');
      option.value = value;
      list.appendChild(option);
    });
  }

  function loadSuggestions() {
    // Подсказки — приятное дополнение: ошибку глотаем, форма работает и без них.
    api('/api/suggestions')
      .then(function (data) {
        fillDatalist('manufacturers-list', data.manufacturers);
        fillDatalist('models-list', data.models);
        fillDatalist('employees-list', data.employees);
      })
      .catch(function () { /* не мешаем заполнять форму */ });
  }

  function setFormValues(values) {
    var form = el('laptop-form');
    FIELD_NAMES.forEach(function (name) {
      var input = form.elements[name];
      if (!input) return;
      var value = values[name];
      // Для статуса это RadioNodeList: присвоение value отмечает нужную кнопку.
      input.value = (value === null || value === undefined) ? '' : String(value);
      markInvalid(input, false);
    });
    if (!values.status) {
      form.elements.status.value = 'in_stock';
    }
  }

  function openForm(options) {
    options = options || {};
    pushHistory();

    state.mode = options.mode || 'create';
    state.qr = options.qr || null;
    state.scanSource = options.scanSource || null;
    state.laptop = options.laptop || state.laptop;

    el('form-title').textContent = state.mode === 'edit' ? 'Редактирование' : 'Новый ноутбук';
    el('form-error').hidden = true;
    el('form-error').textContent = '';

    var notice = el('form-notice');
    if (options.notice) {
      notice.textContent = options.notice;
      notice.hidden = false;
    } else {
      notice.hidden = true;
    }

    setFormValues(options.values || {});

    var rawBox = el('qr-raw-box');
    var raw = state.qr ? state.qr.qr_raw : (state.mode === 'edit' && state.laptop ? state.laptop.qr_raw : null);
    if (raw) {
      el('qr-raw-value').textContent = raw;
      rawBox.hidden = false;
    } else {
      rawBox.hidden = true;
    }

    setBackButton(goBack);
    setMainButton('Сохранить', submitForm);
    showScreen('form');
    loadSuggestions();
  }

  function collectForm() {
    var form = el('laptop-form');
    var values = {};
    FIELD_NAMES.forEach(function (name) {
      var input = form.elements[name];
      values[name] = input ? input.value.trim() : '';
    });
    return values;
  }

  function showFormError(message) {
    var box = el('form-error');
    box.textContent = message;
    box.hidden = false;
  }

  function withProgress(promise) {
    if (tg && tg.MainButton) tg.MainButton.showProgress(false);
    el('btn-save-fallback').disabled = true;
    return promise.then(function (result) {
      if (tg && tg.MainButton) tg.MainButton.hideProgress();
      el('btn-save-fallback').disabled = false;
      return result;
    }, function (error) {
      if (tg && tg.MainButton) tg.MainButton.hideProgress();
      el('btn-save-fallback').disabled = false;
      throw error;
    });
  }

  function submitForm() {
    var values = collectForm();
    var form = el('laptop-form');

    markInvalid(form.elements.serial_number, false);
    el('form-error').hidden = true;

    if (!values.serial_number) {
      markInvalid(form.elements.serial_number, true);
      form.elements.serial_number.focus();
      showFormError('Серийный номер обязателен.');
      return;
    }

    var request;
    if (state.mode === 'edit' && state.laptop) {
      request = api('/api/laptops/' + state.laptop.id, { method: 'PATCH', body: values });
    } else {
      var payload = {};
      FIELD_NAMES.forEach(function (name) { payload[name] = values[name]; });
      payload.source = state.qr ? (state.scanSource || 'qr') : 'manual';
      if (state.qr) payload.qr_raw = state.qr.qr_raw;
      request = api('/api/laptops', { method: 'POST', body: payload });
    }

    withProgress(request).then(function (laptop) {
      state.history = [];
      state.qr = null;
      state.scanSource = null;
      openCard(laptop, state.mode === 'edit' ? 'Изменения сохранены.' : 'Ноутбук сохранён.');
    }).catch(function (error) {
      if (error && error.status === 409 && error.data && error.data.existing) {
        state.history = [];
        state.qr = null;
        state.scanSource = null;
        openCard(error.data.existing, 'Такой серийный номер уже есть в базе — показываю существующую запись.');
        return;
      }
      if (error && (error.status === 422 || error.status === 400)) {
        showFormError(error.message);
        return;
      }
      handleError(error);
    });
  }

  // --- карточка -----------------------------------------------------------

  function openCard(laptop, notice, skipHistory) {
    if (!skipHistory) pushHistory();
    state.laptop = laptop;
    state.mode = 'view';

    var noticeBox = el('card-notice');
    if (notice) {
      noticeBox.textContent = notice;
      noticeBox.hidden = false;
    } else {
      noticeBox.hidden = true;
    }

    var name = [laptop.manufacturer, laptop.model].filter(Boolean).join(' ');
    el('card-title').textContent = name || 'Без модели';
    el('card-serial').textContent = laptop.serial_number;
    el('card-hero').setAttribute('data-status', laptop.status);
    var pill = el('card-status');
    pill.setAttribute('data-status', laptop.status);
    pill.textContent = laptop.status_label;

    var view = {};
    Object.keys(laptop).forEach(function (key) { view[key] = laptop[key]; });
    view.source_label = SOURCE_LABELS[laptop.source] || laptop.source;
    view.created_at_label = formatDate(laptop.created_at);
    view.updated_at_label = formatDate(laptop.updated_at);

    var container = el('card-fields');
    container.textContent = '';
    CARD_SECTIONS.forEach(function (section) {
      var list = document.createElement('dl');
      list.className = 'section';
      section[1].forEach(function (row) {
        var value = view[row[0]];
        if (value === null || value === undefined || value === '') return;
        var wrapper = document.createElement('div');
        wrapper.className = 'row' + (row[2] === 'stack' ? ' row--stack' : '');
        var term = document.createElement('dt');
        term.textContent = row[1];
        var definition = document.createElement('dd');
        definition.textContent = String(value);
        if (row[0] === 'serial_number' || row[0] === 'qr_raw') definition.className = 'mono';
        wrapper.appendChild(term);
        wrapper.appendChild(definition);
        list.appendChild(wrapper);
      });
      if (!list.children.length) return;
      var title = document.createElement('h2');
      title.className = 'section-title';
      title.textContent = section[0];
      container.appendChild(title);
      container.appendChild(list);
    });

    setBackButton(goBack);
    setMainButton('Редактировать', function () {
      openForm({ mode: 'edit', laptop: laptop, values: laptop });
    });
    showScreen('card');
  }

  // --- сканирование: QR и штрих-коды --------------------------------------

  // Декодер весит 328 КБ, поэтому грузится лениво — при первом сканировании,
  // а не при каждом открытии приложения.
  var ZXING_SRC = 'vendor/zxing.min.js?v=1';

  // Форматы с наклеек ноутбуков и инвентарных меток. Чем короче список,
  // тем быстрее разбирается каждый кадр.
  var SCAN_FORMATS = [
    'QR_CODE', 'DATA_MATRIX', 'CODE_128', 'CODE_39', 'CODE_93',
    'ITF', 'CODABAR', 'EAN_13', 'EAN_8', 'UPC_A', 'UPC_E'
  ];

  // Разрешение по умолчанию у getUserMedia — 640×480. QR в нём читается, а вот
  // тонкие полосы Code 128 сливаются, поэтому просим максимум из доступного.
  // Телефон отдаст ближайшее, что умеет: ideal не роняет запрос, если 1080p нет.
  var CAMERA_CONSTRAINTS = {
    video: {
      facingMode: { ideal: 'environment' },
      width: { ideal: 1920 },
      height: { ideal: 1080 },
      advanced: [{ focusMode: 'continuous' }]
    }
  };

  function cameraSupported() {
    return !!(navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === 'function');
  }

  function nativeQrSupported() {
    return !!(tg && typeof tg.showScanQrPopup === 'function' &&
      (typeof tg.isVersionAtLeast !== 'function' || tg.isVersionAtLeast('6.4')));
  }

  function loadZxing() {
    if (window.ZXing) return Promise.resolve(window.ZXing);
    if (state.zxingPromise) return state.zxingPromise;

    state.zxingPromise = new Promise(function (resolve, reject) {
      var script = document.createElement('script');
      script.src = ZXING_SRC;
      script.async = true;
      script.onload = function () {
        if (window.ZXing) resolve(window.ZXing);
        else reject(new Error('Декодер загрузился, но не инициализировался.'));
      };
      script.onerror = function () {
        state.zxingPromise = null; // дать шанс повторной попытке
        reject(new Error('Не удалось загрузить декодер кодов.'));
      };
      document.head.appendChild(script);
    });
    return state.zxingPromise;
  }

  function buildHints(ZXing) {
    var hints = new Map();
    var formats = [];
    SCAN_FORMATS.forEach(function (name) {
      if (ZXing.BarcodeFormat[name] !== undefined) formats.push(ZXing.BarcodeFormat[name]);
    });
    hints.set(ZXing.DecodeHintType.POSSIBLE_FORMATS, formats);
    // Медленнее на кадр, но заметно упорнее на смазанных и мелких штрих-кодах.
    hints.set(ZXing.DecodeHintType.TRY_HARDER, true);
    return hints;
  }

  function videoTrack(stream) {
    if (!stream || typeof stream.getVideoTracks !== 'function') return null;
    return stream.getVideoTracks()[0] || null;
  }

  function trackSettings(stream) {
    var track = videoTrack(stream);
    if (!track || typeof track.getSettings !== 'function') return {};
    try { return track.getSettings() || {}; } catch (e) { return {}; }
  }

  function tuneCamera(stream) {
    // Многие Android-камеры стартуют с фиксированным фокусом: наклейка вблизи
    // остаётся мыльной. Просим непрерывный автофокус, если устройство умеет.
    var track = videoTrack(stream);
    if (!track || typeof track.applyConstraints !== 'function') return;

    var capabilities = {};
    try {
      if (typeof track.getCapabilities === 'function') capabilities = track.getCapabilities() || {};
    } catch (e) { capabilities = {}; }

    var modes = capabilities.focusMode || [];
    if (!modes.length || modes.indexOf('continuous') === -1) return;

    track.applyConstraints({ advanced: [{ focusMode: 'continuous' }] })
      .catch(function () { /* не критично: сканируем как есть */ });
  }

  function describeScanner(stream, decoderName, quiet) {
    var settings = trackSettings(stream);
    // Длинная сторона: в портретной ориентации width — это короткая сторона,
    // и сравнивать с порогом нужно именно максимум.
    var longest = Math.max(settings.width || 0, settings.height || 0);
    var size = (settings.width && settings.height)
      ? settings.width + '×' + settings.height
      : 'разрешение неизвестно';

    el('scan-info').textContent = 'Камера ' + size + ' · сканер: ' + decoderName;

    if (!quiet && longest && longest < 1280) {
      scanStatus('Камера даёт мало пикселей — штрих-код может не прочитаться. ' +
        'Поднеси ближе или введи данные вручную.');
    }
  }

  // Системный детектор Chromium (Android): для 1D-кодов он надёжнее ZXing
  // и ничего не скачивает. На iOS его нет — там остаётся ZXing.
  var NATIVE_FORMATS = [
    'qr_code', 'data_matrix', 'code_128', 'code_39', 'code_93',
    'itf', 'codabar', 'ean_13', 'ean_8', 'upc_a', 'upc_e'
  ];
  var NATIVE_TICK_MS = 120;

  function nativeDetector() {
    if (typeof window.BarcodeDetector !== 'function' ||
        typeof window.BarcodeDetector.getSupportedFormats !== 'function') {
      return Promise.resolve(null);
    }
    return window.BarcodeDetector.getSupportedFormats()
      .then(function (supported) {
        var formats = NATIVE_FORMATS.filter(function (name) {
          return supported.indexOf(name) !== -1;
        });
        // Без 1D-форматов смысла в нативном детекторе нет: QR читает и ZXing.
        if (formats.indexOf('code_128') === -1) return null;
        return new window.BarcodeDetector({ formats: formats });
      })
      .catch(function () { return null; });
  }

  function prepareVideo(video) {
    // iOS проигрывает поток камеры только беззвучно и встроенно, иначе
    // play() отклоняется или видео уходит в полноэкранный плеер.
    video.muted = true;
    video.playsInline = true;
    video.setAttribute('muted', '');
    video.setAttribute('playsinline', '');
    video.setAttribute('webkit-playsinline', '');
    video.setAttribute('autoplay', '');
  }

  function attachStream(video, stream) {
    prepareVideo(video);
    video.srcObject = stream;
    var played = video.play();
    return (played && typeof played.then === 'function')
      ? played.catch(function () { /* не дали запустить — цикл попросит нажать на видео */ })
      : Promise.resolve();
  }

  function runNativeLoop(detector, video) {
    function tick() {
      state.scanTimer = null;
      if (state.screen !== 'scan' || !state.stream) return;

      detector.detect(video).then(function (codes) {
        if (state.screen !== 'scan' || !state.stream) return;
        if (codes && codes.length) {
          var code = codes[0];
          stopScanner();
          handleScanned(code.rawValue, code.format === 'qr_code' ? 'qr' : 'barcode');
          return;
        }
        state.scanTimer = window.setTimeout(tick, NATIVE_TICK_MS);
      }).catch(function () {
        // Кадр ещё не готов или детектор споткнулся — пробуем следующий.
        if (state.screen === 'scan' && state.stream) {
          state.scanTimer = window.setTimeout(tick, NATIVE_TICK_MS);
        }
      });
    }
    tick();
  }

  // --- ZXing: свой цикл распознавания ----------------------------------------
  //
  // Готовый decodeFromStream из ZXing на iPhone не работает:
  //  - холст под кадр создаётся один раз по videoWidth первого кадра, а iOS
  //    успевает начать воспроизведение до того, как сообщит размер, — холст
  //    получается 0×0 и getImageData бросает исключение;
  //  - любое исключение, кроме «код не найден», молча останавливает цикл;
  //  - если iOS отклонил play(), ZXing вечно ждёт события playing.
  // Поэтому кадры рисуем и разбираем сами, а ZXing используем только как
  // декодер одного изображения.

  var ZXING_TICK_MS = 120;
  // Зона рамки — в исходном разрешении (до 1280 px): тонким полосам Code 128
  // нужны пиксели. Полный кадр — уменьшенным: QR крупный, а так быстрее.
  var CROP_MAX_SIDE = 1280;
  var FULL_MAX_SIDE = 960;
  // Сколько ждать, пока видео пойдёт само, прежде чем просить нажать на него.
  var PLAY_GRACE_MS = 1500;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  // Прямоугольник рамки-прицела в координатах кадра камеры. Видео показано с
  // object-fit: cover — масштабировано и обрезано по центру, это учитываем.
  function frameRegion(video) {
    var vw = video.videoWidth;
    var vh = video.videoHeight;
    var box = video.getBoundingClientRect();
    var frame = document.querySelector('.scan-frame');
    if (!frame || !box.width || !box.height) return null;
    var rect = frame.getBoundingClientRect();

    var scale = Math.max(box.width / vw, box.height / vh);
    var offsetX = (box.width - vw * scale) / 2;
    var offsetY = (box.height - vh * scale) / 2;
    // Запас вокруг рамки: код редко попадает в неё точно.
    var marginX = rect.width * 0.1;
    var marginY = rect.height * 0.4;

    var left = clamp((rect.left - box.left - marginX - offsetX) / scale, 0, vw);
    var top = clamp((rect.top - box.top - marginY - offsetY) / scale, 0, vh);
    var right = clamp((rect.right - box.left + marginX - offsetX) / scale, 0, vw);
    var bottom = clamp((rect.bottom - box.top + marginY - offsetY) / scale, 0, vh);
    if (right - left < 32 || bottom - top < 32) return null;
    return { sx: left, sy: top, sw: right - left, sh: bottom - top };
  }

  function drawFrame(canvas, context, video, region, maxSide) {
    var sx = 0;
    var sy = 0;
    var sw = video.videoWidth;
    var sh = video.videoHeight;
    if (region) {
      sx = region.sx;
      sy = region.sy;
      sw = region.sw;
      sh = region.sh;
    }
    var scale = Math.min(1, maxSide / Math.max(sw, sh));
    var width = Math.max(1, Math.round(sw * scale));
    var height = Math.max(1, Math.round(sh * scale));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    context.drawImage(video, sx, sy, sw, sh, 0, 0, width, height);
  }

  function decodeCanvas(ZXing, reader, canvas) {
    var source = new ZXing.HTMLCanvasElementLuminanceSource(canvas);
    var bitmap = new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(source));
    return reader.decodeWithState(bitmap);
  }

  // «Код в кадре не найден» — штатный исход для большинства кадров.
  function isMissError(ZXing, error) {
    return error instanceof ZXing.NotFoundException ||
      error instanceof ZXing.ChecksumException ||
      error instanceof ZXing.FormatException;
  }

  function runZxingLoop(ZXing, reader, video, stream) {
    var canvas = document.createElement('canvas');
    var context = null;
    try {
      context = canvas.getContext('2d', { willReadFrequently: true });
    } catch (e) { context = null; }
    context = context || canvas.getContext('2d');

    var startedAt = Date.now();
    var frames = 0;
    var askedToTap = false;
    var reportedError = false;

    function step() {
      state.scanTimer = null;
      if (state.screen !== 'scan' || !state.stream) return;

      var ready = !video.paused && video.readyState >= 2 && video.videoWidth > 0;
      if (!ready) {
        if (!askedToTap && Date.now() - startedAt > PLAY_GRACE_MS) {
          askedToTap = true;
          scanStatus('Камера не включилась сама — нажми на окно камеры.');
        }
        state.scanTimer = window.setTimeout(step, ZXING_TICK_MS);
        return;
      }
      if (askedToTap) {
        askedToTap = false;
        scanStatus(null);
      }

      // Два кадра из трёх — зона рамки (штрих-коды), третий — весь кадр,
      // чтобы QR читался, даже если его держат не в рамке.
      var full = frames % 3 === 2;
      frames += 1;
      try {
        drawFrame(canvas, context, video, full ? null : frameRegion(video),
          full ? FULL_MAX_SIDE : CROP_MAX_SIDE);
        var result = decodeCanvas(ZXing, reader, canvas);
        stopScanner();
        handleScanned(result.getText(), codeKind(ZXing, result));
        return;
      } catch (error) {
        if (!isMissError(ZXing, error) && !reportedError) {
          // Не останавливаемся: следующий кадр может пройти. Причину — в консоль.
          reportedError = true;
          if (window.console) window.console.warn('Ошибка разбора кадра', error);
        }
      }

      if (frames % 10 === 0) describeScanner(stream, 'ZXing · кадров: ' + frames, true);
      state.scanTimer = window.setTimeout(step, ZXING_TICK_MS);
    }
    step();
  }

  function startZxing(stream, video) {
    if (!window.ZXing) scanStatus('Загружаю сканер…');
    return loadZxing().then(function (ZXing) {
      if (state.screen !== 'scan' || !state.stream) return false;

      var reader = new ZXing.MultiFormatReader();
      reader.setHints(buildHints(ZXing));
      state.reader = reader;
      scanStatus(null); // убрать «Загружаю сканер…»
      describeScanner(stream, 'ZXing');
      runZxingLoop(ZXing, reader, video, stream);
      return true;
    });
  }

  function startDecoding(stream) {
    var video = el('scan-video');
    // Видео запускаем сразу: на iOS чем дальше от нажатия кнопки, тем выше
    // шанс, что play() отклонят.
    return attachStream(video, stream).then(function () {
      if (state.screen !== 'scan') return false;
      return nativeDetector().then(function (detector) {
        if (state.screen !== 'scan') return false;
        if (!detector) return startZxing(stream, video);
        describeScanner(stream, 'системный');
        runNativeLoop(detector, video);
        return true;
      });
    });
  }

  function codeKind(ZXing, result) {
    var name = '';
    try { name = ZXing.BarcodeFormat[result.getBarcodeFormat()]; } catch (e) { name = ''; }
    return name === 'QR_CODE' ? 'qr' : 'barcode';
  }

  function scanStatus(message) {
    var box = el('scan-status');
    if (!message) {
      box.hidden = true;
      box.textContent = '';
      return;
    }
    box.textContent = message;
    box.hidden = false;
  }

  function stopScanner() {
    if (state.scanTimer) {
      window.clearTimeout(state.scanTimer);
      state.scanTimer = null;
    }
    if (state.reader) {
      try { state.reader.reset(); } catch (e) { /* уже остановлен */ }
      state.reader = null;
    }
    if (state.stream) {
      try {
        state.stream.getTracks().forEach(function (track) { track.stop(); });
      } catch (e) { /* поток уже закрыт */ }
      state.stream = null;
    }
    var video = el('scan-video');
    if (video && video.srcObject) {
      try {
        video.srcObject.getTracks().forEach(function (track) { track.stop(); });
      } catch (e) { /* поток уже закрыт */ }
      video.srcObject = null;
    }
  }

  function cameraErrorMessage(error) {
    var name = (error && error.name) ? error.name : '';
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      return 'Нет доступа к камере. Разреши камеру для Telegram в настройках телефона ' +
        'и попробуй снова — или введи данные вручную.';
    }
    if (name === 'NotFoundError' || name === 'OverconstrainedError') {
      return 'Камера не найдена. Введи данные вручную.';
    }
    if (name === 'NotReadableError') {
      return 'Камера занята другим приложением. Закрой его и попробуй снова.';
    }
    return (error && error.message) ? error.message : 'Не удалось включить камеру.';
  }

  function openScanner() {
    pushHistory();
    state.scanSource = null;
    el('btn-scan-native').hidden = true;
    el('scan-info').textContent = '';
    scanStatus('Запрашиваю доступ к камере…');
    setMainButton(null, null);
    setBackButton(goBack);
    showScreen('scan');

    if (typeof window.BarcodeDetector !== 'function') {
      // Системного детектора нет (iPhone) — понадобится ZXing, грузим заранее.
      loadZxing().catch(function () { /* ошибку покажет startZxing */ });
    }

    navigator.mediaDevices.getUserMedia(CAMERA_CONSTRAINTS)
      .then(function (stream) {
        if (state.screen !== 'scan') {
          // Успели уйти с экрана, пока спрашивали доступ.
          stream.getTracks().forEach(function (track) { track.stop(); });
          return false;
        }
        state.stream = stream;
        tuneCamera(stream);
        scanStatus(null);
        return startDecoding(stream);
      })
      .catch(function (error) {
        stopScanner();
        if (state.screen !== 'scan') return;
        scanStatus(cameraErrorMessage(error));
        el('scan-info').textContent = '';
        el('btn-scan-native').hidden = !nativeQrSupported();
      });
  }

  function startNativeQrScan() {
    if (!nativeQrSupported()) return;
    tg.showScanQrPopup({ text: 'Наведи камеру на QR-код ноутбука' }, function (text) {
      if (!text) return true;
      handleScanned(text, 'qr');
      return true; // true закрывает попап сканера
    });
  }

  function handleScanned(text, kind) {
    kind = kind === 'barcode' ? 'barcode' : 'qr';
    api('/api/qr/parse', { method: 'POST', body: { raw: text } })
      .then(function (parsed) {
        if (parsed.existing) {
          openCard(parsed.existing, 'Этот ноутбук уже есть в базе — новая запись не создана.');
          return;
        }
        var recognized = Object.keys(parsed.fields).length;
        var what = kind === 'barcode' ? 'штрих-кода' : 'QR-кода';
        var notice = recognized
          ? 'Распознано полей: ' + recognized + '. Проверь и дополни данные, затем сохрани.'
          : 'Не удалось распознать поля из ' + what + '. Заполни их вручную — ' +
            'содержимое кода сохранится.';
        openForm({
          mode: 'create', qr: parsed, scanSource: kind,
          values: parsed.fields, notice: notice
        });
      })
      .catch(handleError);
  }

  function startScan() {
    el('scan-notice').hidden = true;

    if (cameraSupported()) {
      openScanner();
      return;
    }
    if (nativeQrSupported()) {
      startNativeQrScan();
      return;
    }

    var message = 'Сканирование недоступно в этой версии Telegram (например, в Telegram Desktop). ' +
      'Открой приложение на телефоне или добавь ноутбук вручную.';
    var notice = el('scan-notice');
    notice.textContent = message;
    notice.hidden = false;
    alertMessage(message);
  }

  // --- запуск -------------------------------------------------------------

  function bindEvents() {
    el('btn-scan').addEventListener('click', startScan);
    el('btn-manual').addEventListener('click', function () {
      openForm({ mode: 'create', values: {} });
    });

    el('btn-scan-cancel').addEventListener('click', goBack);
    el('btn-scan-manual').addEventListener('click', function () {
      openForm({ mode: 'create', values: {} });
    });
    el('scan-video').parentNode.addEventListener('click', function () {
      var video = el('scan-video');
      if (!video.srcObject || !video.paused) return;
      var played = video.play();
      if (played && typeof played.catch === 'function') played.catch(function () {});
    });

    el('btn-scan-native').addEventListener('click', function () {
      stopScanner();
      startNativeQrScan();
    });

    el('laptop-form').addEventListener('submit', function (event) {
      event.preventDefault();
      submitForm();
    });

    el('btn-edit-fallback').addEventListener('click', function () {
      if (state.laptop) {
        openForm({ mode: 'edit', laptop: state.laptop, values: state.laptop });
      }
    });

    el('search').addEventListener('input', function () {
      el('search-clear').hidden = !el('search').value;
      window.clearTimeout(state.searchTimer);
      state.searchTimer = window.setTimeout(function () { loadList(); }, 300);
    });

    el('search').addEventListener('keydown', function (event) {
      if (event.key === 'Enter') el('search').blur(); // спрятать клавиатуру
    });

    el('search-clear').addEventListener('click', function () {
      el('search').value = '';
      el('search-clear').hidden = true;
      window.clearTimeout(state.searchTimer);
      loadList();
    });

    Array.prototype.forEach.call(el('search-by').children, function (button) {
      button.addEventListener('click', function () {
        setSearchBy(button.getAttribute('data-by'));
      });
    });

    el('btn-sort').addEventListener('click', toggleSort);
    el('btn-more').addEventListener('click', function () { loadList(true); });

    el('btn-retry').addEventListener('click', start);

    el('btn-copy-id').addEventListener('click', function () {
      var value = el('denied-id').textContent;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(value).then(function () {
          alertMessage('ID скопирован: ' + value);
        }, function () {
          alertMessage('Твой Telegram ID: ' + value);
        });
      } else {
        alertMessage('Твой Telegram ID: ' + value);
      }
    });
  }

  function start() {
    showScreen('loading');
    api('/api/meta')
      .then(function (meta) {
        state.statuses = meta.statuses;
        fillStatusOptions();
        return api('/api/me');
      })
      .then(function (me) {
        renderMe(me);
        return openList();
      })
      .catch(handleError);
  }

  function renderMe(me) {
    var avatar = el('me-avatar');
    var words = String((me && me.name) || '').replace('@', '').split(/\s+/).filter(Boolean);
    var initials = words.slice(0, 2).map(function (word) { return word.charAt(0); }).join('');
    avatar.textContent = initials.toUpperCase() || '?';
    avatar.title = (me && me.name ? me.name : '') + (me && me.is_admin ? ' · администратор' : '');
    avatar.hidden = false;
  }

  function init() {
    bindEvents();

    if (!tg || !tg.initData) {
      showError('Это приложение открывается только из Telegram: нажми кнопку меню у бота.');
      return;
    }

    applyTheme();
    tg.onEvent('themeChanged', applyTheme);
    document.body.classList.add('hide-fallback');
    tg.ready();
    tg.expand();
    start();
  }

  init();
})();
