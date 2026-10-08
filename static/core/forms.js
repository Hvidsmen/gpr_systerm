/* Searchable selects retain native form values, validation and change events. */
(() => {
  const normalize = text => String(text).toLocaleLowerCase('ru').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/ё/g, 'е').trim();
  const matches = (text, query) => normalize(query).split(/\s+/).filter(Boolean).every(word => normalize(text).includes(word));
  if (typeof module !== 'undefined' && module.exports) module.exports = {normalize, matches};
  if (typeof document === 'undefined') return;
  const instances = new Set();
  let active = null, sequence = 0, queued = false;

  class SearchSelect {
    constructor(select) {
      this.select = select;
      this.limit = 100;
      this.shell = document.createElement('div');
      this.shell.className = 'search-select-shell';
      this.button = document.createElement('button');
      this.button.type = 'button';
      this.button.className = 'search-select-button';
      this.button.setAttribute('role', 'combobox');
      this.button.setAttribute('aria-haspopup', 'listbox');
      this.button.setAttribute('aria-expanded', 'false');
      this.label = document.createElement('span');
      this.label.className = 'search-select-label';
      const arrow = document.createElement('span');
      arrow.className = 'search-select-chevron';
      arrow.setAttribute('aria-hidden', 'true');
      this.button.append(this.label, arrow);
      this.validation = document.createElement('div');
      this.validation.className = 'search-select-validation';
      this.validation.setAttribute('role', 'alert');
      this.validation.hidden = true;
      this.shell.append(this.button, this.validation);
      select.after(this.shell);
      select.classList.add('search-select-native');
      select.dataset.searchEnhanced = 'true';
      select.tabIndex = -1;
      this.popup = document.createElement('div');
      this.popup.className = 'search-select-popup';
      this.popup.hidden = true;
      this.search = document.createElement('input');
      this.search.type = 'search';
      this.search.autocomplete = 'off';
      this.search.placeholder = 'Поиск в списке…';
      this.search.className = 'search-select-search';
      this.options = document.createElement('div');
      this.options.className = 'search-select-options';
      this.options.id = 'search-select-options-' + (++sequence);
      this.options.setAttribute('role', 'listbox');
      this.button.setAttribute('aria-controls', this.options.id);
      this.search.setAttribute('aria-controls', this.options.id);
      this.clear = document.createElement('button');
      this.clear.type = 'button';
      this.clear.className = 'search-select-clear';
      this.clear.textContent = 'Снять выбор';
      this.popup.append(this.search, this.options, this.clear);
      (select.closest('.modal') || document.body).append(this.popup);
      this.button.addEventListener('click', () => this.isOpen ? this.close() : this.open());
      this.button.addEventListener('keydown', event => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {event.preventDefault(); this.open();}
      });
      this.search.addEventListener('input', () => {this.limit = 100; this.render();});
      this.popup.addEventListener('keydown', event => this.keydown(event));
      this.clear.addEventListener('click', () => {
        if (this.disabled()) return;
        [...select.options].forEach(option => option.selected = false);
        this.changed();
        this.search.focus();
      });
      select.addEventListener('change', () => this.sync());
      select.addEventListener('focus', () => this.button.focus());
      select.addEventListener('invalid', event => {
        event.preventDefault();
        this.shell.classList.add('search-select-invalid');
        this.validation.textContent = select.validationMessage;
        this.validation.hidden = false;
        this.button.focus();
      });
      this.observer = new MutationObserver(() => this.sync());
      this.observer.observe(select, {childList:true, subtree:true, characterData:true, attributes:true,
        attributeFilter:['disabled', 'required', 'multiple', 'selected', 'hidden', 'label', 'style', 'aria-invalid']});
      instances.add(this);
      this.sync();
    }
    disabled() {return this.select.matches(':disabled');}
    get isOpen() {return !this.popup.hidden;}
    sync() {
      if (!this.select.isConnected) {
        this.close(); this.popup.remove(); this.shell.remove(); this.observer.disconnect(); instances.delete(this); return;
      }
      const selected = [...this.select.selectedOptions];
      const text = selected.map(option => !option.value && /^[-–—]+$/.test(option.textContent.trim()) ? 'Выберите значение' : option.textContent.trim());
      const label = this.select.labels?.[0]?.textContent.trim() || this.select.getAttribute('aria-label') || 'Выбор значения';
      const value = this.select.multiple ? (text.length ? text.slice(0, 2).join(', ') + (text.length > 2 ? ` · ещё ${text.length - 2}` : '') : 'Выберите значения') : (text[0] || 'Выберите значение');
      if (this.label.textContent !== value) this.label.textContent = value;
      this.button.title = text.join(', ') || label;
      this.button.setAttribute('aria-label', label + ': ' + value);
      this.search.setAttribute('aria-label', 'Поиск: ' + label);
      this.options.setAttribute('aria-label', label);
      this.options.setAttribute('aria-multiselectable', String(this.select.multiple));
      this.button.setAttribute('aria-required', String(this.select.required));
      const disabled = this.disabled();
      if (this.button.disabled !== disabled) this.button.disabled = disabled;
      this.shell.hidden = this.select.hidden || this.select.style.display === 'none';
      this.clear.hidden = !this.select.multiple;
      const described = this.select.getAttribute('aria-describedby');
      if (described) this.button.setAttribute('aria-describedby', described);
      this.button.setAttribute('aria-invalid', this.select.getAttribute('aria-invalid') || String(!this.validation.hidden));
      if (this.button.disabled || this.shell.hidden) this.close();
      else if (this.isOpen) this.render();
    }
    open() {
      this.sync();
      if (this.button.disabled || this.shell.hidden) return;
      if (active && active !== this) active.close();
      active = this;
      this.search.value = '';
      this.limit = 100;
      this.popup.hidden = false;
      this.button.setAttribute('aria-expanded', 'true');
      this.render(); this.position(); this.search.focus({preventScroll:true});
    }
    close(returnFocus = false) {
      this.popup.hidden = true;
      this.button.setAttribute('aria-expanded', 'false');
      if (active === this) active = null;
      if (returnFocus) this.button.focus({preventScroll:true});
    }
    position() {
      if (!this.isOpen) return;
      const rect = this.button.getBoundingClientRect();
      if (!rect.width || !rect.height) {this.close(); return;}
      const width = Math.min(Math.max(rect.width, 280), window.innerWidth - 20);
      const below = window.innerHeight - rect.bottom - 16;
      const above = rect.top - 16;
      const upwards = below < 180 && above > below;
      const height = Math.min(360, Math.max(120, upwards ? above : below));
      this.popup.style.width = width + 'px';
      this.popup.style.maxHeight = height + 'px';
      this.popup.style.left = Math.max(10, Math.min(rect.left, window.innerWidth - width - 10)) + 'px';
      this.popup.style.top = upwards ? 'auto' : Math.max(10, rect.bottom + 5) + 'px';
      this.popup.style.bottom = upwards ? Math.max(10, window.innerHeight - rect.top + 5) + 'px' : 'auto';
    }
    changed() {
      this.shell.classList.remove('search-select-invalid');
      this.validation.hidden = true;
      this.select.dispatchEvent(new Event('input', {bubbles:true}));
      this.select.dispatchEvent(new Event('change', {bubbles:true}));
      this.sync();
    }
    render() {
      const options = [...this.select.options].filter(option => !option.hidden && matches(option.textContent + ' ' + (option.parentElement.tagName === 'OPTGROUP' ? option.parentElement.label : ''), this.search.value));
      this.options.replaceChildren();
      let previousGroup = null;
      options.slice(0, this.limit).forEach(option => {
        const group = option.parentElement.tagName === 'OPTGROUP' ? option.parentElement : null;
        if (group && group !== previousGroup) {
          const heading = document.createElement('div'); heading.className = 'search-select-group'; heading.textContent = group.label;
          heading.setAttribute('role', 'presentation'); this.options.append(heading);
        }
        previousGroup = group;
        const item = document.createElement('button');
        item.type = 'button'; item.className = 'search-select-option'; item.tabIndex = -1;
        item.setAttribute('role', 'option'); item.setAttribute('aria-selected', String(option.selected));
        item.disabled = option.disabled || Boolean(group?.disabled);
        const mark = document.createElement('span'); mark.className = 'search-select-mark'; mark.textContent = option.selected ? '✓' : ''; mark.setAttribute('aria-hidden', 'true');
        const text = document.createElement('span'); text.textContent = !option.value && /^[-–—]+$/.test(option.textContent.trim()) ? 'Не выбрано' : (option.textContent || 'Не выбрано');
        item.append(mark, text);
        item.addEventListener('click', () => {
          if (this.disabled() || item.disabled) return;
          if (this.select.multiple) option.selected = !option.selected;
          else this.select.selectedIndex = option.index;
          this.changed();
          if (this.select.multiple) this.search.focus({preventScroll:true});
          else this.close(true);
        });
        this.options.append(item);
      });
      if (!options.length) {
        const empty = document.createElement('div'); empty.className = 'search-select-empty'; empty.setAttribute('role', 'status'); empty.textContent = 'Ничего не найдено'; this.options.append(empty);
      } else if (options.length > this.limit) {
        const more = document.createElement('button'); more.type = 'button'; more.className = 'search-select-more';
        more.textContent = `Показать ещё · найдено ${options.length}`;
        more.addEventListener('click', () => {this.limit += 100; this.render();}); this.options.append(more);
      }
      this.position();
    }
    keydown(event) {
      if (event.key === 'Tab') {this.close(true); return;}
      if (event.key === 'Escape') {event.preventDefault(); this.close(true); return;}
      const items = [...this.options.querySelectorAll('[role="option"]:not(:disabled)')];
      const index = items.indexOf(document.activeElement);
      if (event.key === 'ArrowDown') {event.preventDefault(); items[Math.min(index + 1, items.length - 1)]?.focus();}
      else if (event.key === 'ArrowUp') {event.preventDefault(); index <= 0 ? this.search.focus() : items[index - 1].focus();}
      else if (event.key === 'Enter' && event.target === this.search) {event.preventDefault(); items[0]?.click();}
      else if (event.target !== this.search && event.key === 'Home') {event.preventDefault(); items[0]?.focus();}
      else if (event.target !== this.search && event.key === 'End') {event.preventDefault(); items.at(-1)?.focus();}
    }
  }

  function styleControls(root) {
    root.querySelectorAll('input:not([type="hidden"]), textarea').forEach(input => {
      if (input.closest('.search-select-popup')) return;
      const check = ['checkbox', 'radio'].includes(input.type);
      input.classList.add(check ? 'form-check-input' : 'form-control');
    });
    root.querySelectorAll('form button:not([class])').forEach(button => button.classList.add('btn', 'btn-primary'));
    root.querySelectorAll('select:not([data-search-enhanced]):not([data-native-select])').forEach(select => new SearchSelect(select));
    root.querySelectorAll('.app-form-fields').forEach(fields => {
      const form = fields.closest('form');
      if (form?.parentElement.classList.contains('main-content') && !form.classList.contains('card')) form.classList.add('app-form-card');
    });
  }
  function syncAll() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {queued = false; instances.forEach(instance => instance.sync());});
  }
  function initialize() {
    const main = document.querySelector('.main-content');
    if (!main) return;
    styleControls(main);
    new MutationObserver(mutations => {
      mutations.forEach(mutation => {
        if (mutation.target.nodeType === 1 && mutation.target.closest('.search-select-popup')) return;
        if (mutation.type === 'attributes') {syncAll(); return;}
        mutation.addedNodes.forEach(node => {
          if (node.nodeType !== 1 || node.closest('.search-select-popup')) return;
          if (node.tagName === 'SELECT' && !node.dataset.searchEnhanced && !node.hasAttribute('data-native-select')) new SearchSelect(node);
          styleControls(node);
        });
        if (mutation.removedNodes.length) syncAll();
      });
    }).observe(main, {childList:true, subtree:true, attributes:true, attributeFilter:['disabled']});
    document.addEventListener('change', syncAll, true);
    document.addEventListener('click', syncAll, true);
    document.addEventListener('reset', () => setTimeout(syncAll, 0), true);
    document.addEventListener('pointerdown', event => {
      if (active && !active.shell.contains(event.target) && !active.popup.contains(event.target)) active.close();
    });
    document.addEventListener('focusin', event => {
      if (active && !active.shell.contains(event.target) && !active.popup.contains(event.target)) active.close();
    });
    window.addEventListener('resize', () => active?.position());
    window.addEventListener('scroll', () => active?.position(), true);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
