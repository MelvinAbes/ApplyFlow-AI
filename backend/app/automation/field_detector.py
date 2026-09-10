import hashlib
import json
from urllib.parse import urldefrag

from playwright.async_api import Locator, Page
from pydantic import BaseModel, Field


class DetectedField(BaseModel):
    selector: str
    question: str
    html_name: str | None = None
    html_id: str | None = None
    autocomplete: str | None = None
    placeholder: str | None = None
    section: str | None = None
    translated_question: str | None = None
    source_language: str | None = None
    canonical_key: str | None = None
    field_type: str
    required: bool = False
    character_limit: int | None = None
    options: list[str] = Field(default_factory=list)
    translated_options: list[str] = Field(default_factory=list)
    translation_confidence: float = 0.0


DETECT_FIELDS_SCRIPT = r"""
root => {
  const scope = root || document;
  const directControls = Array.from(scope.querySelectorAll(
    'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select'
  )).filter(el => !el.disabled && (el.offsetParent !== null || el.type === 'file'));
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const resumeWords = /\b(?:resume|cv|curriculum vitae|lebenslauf)\b/i;
  const otherDocumentWords = /\b(?:cover letter|certificate|attachment|additional document|portfolio|photo|avatar)\b/i;
  const explicitLabel = el => el.id
    ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
    : null;
  const referencedText = (el, attribute) => clean(
    (el.getAttribute(attribute) || '').split(/\s+/)
      .map(id => document.getElementById(id)?.textContent || '').join(' ')
  );
  const nativeLabels = el => clean(Array.from(el.labels || []).map(label => label.textContent || '').join(' '));
  const accessibleName = el => clean(
    referencedText(el, 'aria-labelledby') || el.getAttribute('aria-label') ||
    nativeLabels(el) || explicitLabel(el)?.textContent || el.closest('label')?.textContent
  );
  const fieldContainerFor = el => el.closest(
    '[data-testid*="field" i], [class*="form-field" i], [class*="field-wrapper" i], '
    + '[class*="input-wrapper" i], [class*="field-"]'
  );
  const sectionFor = el => {
    let ancestor = el.parentElement;
    while (ancestor && ancestor !== scope && ancestor !== document.documentElement) {
      if (ancestor.matches('section, fieldset, [role="group"], [class*="section" i], [class*="panel" i]')) {
        const heading = ancestor.querySelector(':scope > legend, :scope > h1, :scope > h2, :scope > h3, :scope > h4');
        const name = clean(
          heading?.textContent || ancestor.getAttribute('aria-label') ||
          referencedText(ancestor, 'aria-labelledby')
        );
        if (name) return name;
      }
      ancestor = ancestor.parentElement;
    }
    return '';
  };
  const optionLabel = el => clean(
    nativeLabels(el) || explicitLabel(el)?.textContent || el.closest('label')?.textContent ||
    el.getAttribute('aria-label') || referencedText(el, 'aria-labelledby') || el.value
  );
  const labelFor = el => {
    const wrapped = el.closest('label');
    const described = referencedText(el, 'aria-describedby');
    const group = el.closest('fieldset');
    const fieldContainer = fieldContainerFor(el);
    const contextual = fieldContainer?.querySelector('label, [id*="label" i], [class*="label" i]');
    const nearby = el.parentElement?.querySelector(':scope > label, :scope > .label, :scope > p');
    return clean(
      accessibleName(el) || wrapped?.textContent || contextual?.textContent || nearby?.textContent ||
      group?.querySelector('legend')?.textContent || el.getAttribute('placeholder') ||
      el.getAttribute('name') || el.id || described
    );
  };

  // A number of application portals keep profile details in a nested native form,
  // but render the document widget alongside it.  A file input is eligible outside
  // that form only when its own small surrounding widget explicitly identifies it as
  // a résumé/CV upload.  This avoids adopting cover letters, certificates, avatars,
  // or arbitrary file pickers elsewhere on the page.
  const resumeUploadContext = el => {
    const own = clean([
      el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('name'), el.id
    ].filter(Boolean).join(' '));
    if (resumeWords.test(own)) return own;
    if (otherDocumentWords.test(own)) return '';
    let container = el.parentElement;
    for (let depth = 0; container && depth < 6; depth += 1, container = container.parentElement) {
      const context = clean([
        container.getAttribute('aria-label'), container.getAttribute('title'),
        container.getAttribute('data-testid'), container.textContent
      ].filter(Boolean).join(' '));
      if (resumeWords.test(context)) return context;
      if (otherDocumentWords.test(context)) return '';
    }
    return '';
  };
  const uploadActionWords = /\b(?:upload|attach|add|choose|select|browse|hochladen|anhängen|hinzufügen|auswählen|datei auswählen)\b/i;
  const visiblyInteractive = el => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return !el.hidden && style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const resumeTriggerContext = el => {
    const own = clean([
      accessibleName(el), el.getAttribute('title'), el.getAttribute('name'), el.id,
      el.textContent
    ].filter(Boolean).join(' '));
    if (otherDocumentWords.test(own)) return '';
    const ownIsResumeTrigger = resumeWords.test(own) && uploadActionWords.test(own);
    let container = el.parentElement;
    for (let depth = 0; container && depth < 5; depth += 1, container = container.parentElement) {
      const context = clean([
        container.getAttribute('aria-label'), container.getAttribute('title'),
        container.getAttribute('data-testid'), container.textContent
      ].filter(Boolean).join(' '));
      if (otherDocumentWords.test(context)) return ownIsResumeTrigger ? own : '';
      if (resumeWords.test(context) && uploadActionWords.test(own)) return context;
    }
    return ownIsResumeTrigger ? own : '';
  };
  const hasRelatedResumeInput = el => {
    let container = el.parentElement;
    for (let depth = 0; container && depth < 7; depth += 1, container = container.parentElement) {
      if (Array.from(container.querySelectorAll('input[type=file]:not(:disabled)')).some(
        input => Boolean(resumeUploadContext(input))
      )) return true;
    }
    return false;
  };
  const resumeWidgetIsRequired = el => {
    let container = el;
    for (let depth = 0; container && depth < 6; depth += 1, container = container.parentElement) {
      const semanticMarker = clean([
        container.id, container.className, container.getAttribute('aria-required')
      ].filter(Boolean).join(' '));
      if (container.required || container.getAttribute('aria-required') === 'true' ||
          /required(?:field|[-_\s]|$)/i.test(semanticMarker)) return true;
      const context = clean([
        container.getAttribute('aria-label'), container.getAttribute('title'),
        container.textContent
      ].filter(Boolean).join(' '));
      if (/(?:\brequired\b|\*)/i.test(context)) return true;
    }
    return false;
  };
  const externalResumeInputs = scope === document ? [] : Array.from(
    document.querySelectorAll('input[type=file]:not(:disabled)')
  ).filter(el => !scope.contains(el) && Boolean(resumeUploadContext(el)));
  // Some application systems create their native file input only when the visible
  // résumé button is clicked. Treat that button as the upload control when it has a
  // clear résumé/CV identity and there is no already-detected file input in its widget.
  const possibleResumeTriggers = Array.from(document.querySelectorAll(
    'button:not(:disabled), [role="button"]:not([aria-disabled="true"])'
  )).filter(el => visiblyInteractive(el) && Boolean(resumeTriggerContext(el)) &&
    !hasRelatedResumeInput(el));
  // Custom upload components often expose an interactive wrapper around their real
  // button. Keep the innermost qualified control so one employer widget produces one
  // résumé field, rather than duplicate review rows for its wrapper and child.
  const customResumeTriggers = possibleResumeTriggers.filter(el => !possibleResumeTriggers.some(
    other => other !== el && el.contains(other)
  ));
  const rawControls = [...directControls, ...externalResumeInputs, ...customResumeTriggers].filter(
    (element, index, controls) => controls.indexOf(element) === index
  );
  const externalResumeSet = new Set(externalResumeInputs);
  const customResumeTriggerSet = new Set(customResumeTriggers);

  const fields = [];
  const seenRadioNames = new Set();
  for (const el of rawControls) {
    const inputType = el.tagName === 'INPUT' ? (el.getAttribute('type') || 'text') : '';
    if (inputType === 'radio' && el.name) {
      if (seenRadioNames.has(el.name)) continue;
      seenRadioNames.add(el.name);
      const radios = rawControls.filter(candidate =>
        candidate.tagName === 'INPUT' && candidate.type === 'radio' && candidate.name === el.name
      );
      const radioGroup = el.closest('[role="radiogroup"]') || el.closest('fieldset');
      const groupQuestion = clean(
        (radioGroup ? accessibleName(radioGroup) : '') ||
        radioGroup?.querySelector(':scope > legend')?.textContent
      );
      const optionSummary = radios.map(optionLabel).filter(Boolean).join(' / ');
      const token = `jaa-${fields.length}`;
      radios.forEach(radio => radio.setAttribute('data-jaa-radio-group', token));
      fields.push({
        selector: `[data-jaa-radio-group="${token}"]`,
        question: groupQuestion || optionSummary || labelFor(el),
        html_name: el.getAttribute('name') || null,
        html_id: el.id || null,
        autocomplete: el.getAttribute('autocomplete') || null,
        placeholder: el.getAttribute('placeholder') || null,
        section: sectionFor(el) || null,
        field_type: 'radio',
        required: radios.some(radio => radio.required || radio.getAttribute('aria-required') === 'true'),
        character_limit: null,
        options: radios.map(optionLabel).filter(Boolean),
      });
      continue;
    }

    const token = `jaa-${fields.length}`;
    el.setAttribute('data-jaa-field', token);
    let type = el.tagName.toLowerCase();
    if (type === 'input') type = inputType;
    // Preserve the precise field label for uploads already inside the selected form.
    // Only a sibling résumé widget needs the surrounding résumé context to give it a
    // trustworthy identity.
    const isFileControl = inputType === 'file' || customResumeTriggerSet.has(el);
    const resumeContext = inputType === 'file' && externalResumeSet.has(el)
      ? resumeUploadContext(el)
      : customResumeTriggerSet.has(el)
        ? resumeTriggerContext(el)
        : '';
    const question = resumeContext || labelFor(el);
    if (el.getAttribute('role') === 'combobox') {
      const dateLike = el.getAttribute('aria-haspopup') === 'dialog' ||
        /date of birth|birth date|geburtsdatum/i.test(question) ||
        /(?:dd|tt)[.\/-](?:mm)[.\/-](?:yyyy|jjjj)/i.test(el.getAttribute('placeholder') || '');
      type = dateLike ? 'date' : 'combobox';
    }
    const options = el.tagName === 'SELECT'
      ? Array.from(el.options)
          .filter(option => option.value && !option.disabled)
          .map(option => clean(option.textContent))
      : [];
    const fieldContainer = fieldContainerFor(el);
    const required = el.required || el.getAttribute('aria-required') === 'true' || Boolean(
      fieldContainer?.querySelector('label[class*="required" i], [aria-required="true"]')
    ) || (
      (externalResumeSet.has(el) || customResumeTriggerSet.has(el)) &&
      (/(?:\brequired\b|\*)/i.test(resumeContext) || resumeWidgetIsRequired(el))
    );
    fields.push({
      selector: `[data-jaa-field="${token}"]`,
      question,
      html_name: el.getAttribute('name') || null,
      html_id: el.id || null,
      autocomplete: el.getAttribute('autocomplete') || null,
      placeholder: el.getAttribute('placeholder') || null,
      section: sectionFor(el) || null,
      field_type: isFileControl ? 'file' : type,
      required,
      character_limit: el.maxLength > 0 ? el.maxLength : null,
      options,
    });
  }
  return fields;
}
"""


async def mark_application_form(page: Page, form: Locator) -> Locator:
    await page.locator("[data-jaa-application-form]").evaluate_all(
        "elements => elements.forEach(element => element.removeAttribute('data-jaa-application-form'))"
    )
    await form.evaluate("element => element.setAttribute('data-jaa-application-form', 'active')")
    return page.locator("form[data-jaa-application-form='active']")


async def detect_fields(page: Page, form: Locator | None = None) -> list[DetectedField]:
    raw_fields = await _detect_raw_fields(page, form)
    fields = [DetectedField.model_validate(field) for field in raw_fields if field.get("question")]
    for field in fields:
        if field.field_type == "combobox":
            field.options = await _detect_combobox_options(page, field.selector)
    return fields


async def rediscover_field(page: Page, field: DetectedField) -> DetectedField:
    """Find a stored field again after a reactive portal replaces its DOM node.

    The ``data-jaa-*`` selectors are deliberately page-local markers rather than selectors
    supplied by an employer. Framework-driven forms can replace a control after any input,
    which removes that marker. Re-running the side-effect-free detection script restores the
    markers; matching by question and control type prevents a shifted field order from silently
    targeting a different control.
    """

    marked_form = page.locator("[data-jaa-application-form='active']")
    scope = marked_form.first if await marked_form.count() == 1 else None
    raw_fields = await _detect_raw_fields(page, scope)
    candidates = [
        DetectedField.model_validate(candidate)
        for candidate in raw_fields
        if candidate.get("question")
    ]
    expected_question = _identity_text(field.question)
    same_identity = [
        candidate
        for candidate in candidates
        if candidate.field_type == field.field_type
        and _identity_text(candidate.question) == expected_question
    ]
    if len(same_identity) == 1:
        match = same_identity[0]
    else:
        same_selector = [
            candidate
            for candidate in (same_identity or candidates)
            if candidate.selector == field.selector
            and candidate.field_type == field.field_type
        ]
        if len(same_selector) != 1:
            raise ValueError(f"Field is no longer present: {field.question}")
        match = same_selector[0]

    # Interpretation and translated choices belong to the stored review record. Only the live
    # selector and raw control metadata are refreshed here.
    match.translated_question = field.translated_question
    match.source_language = field.source_language
    match.canonical_key = field.canonical_key
    match.options = field.options or match.options
    match.translated_options = field.translated_options
    match.translation_confidence = field.translation_confidence
    return match


async def _detect_raw_fields(page: Page, form: Locator | None = None) -> list[dict]:
    return (
        await form.evaluate(DETECT_FIELDS_SCRIPT)
        if form is not None
        else await page.evaluate(DETECT_FIELDS_SCRIPT)
    )


def _identity_text(value: str) -> str:
    return " ".join(value.casefold().split())


async def _detect_combobox_options(page: Page, selector: str) -> list[str]:
    control = page.locator(selector)
    if await control.count() != 1:
        return []
    try:
        associated = await associated_option_locator(page, control)
        if associated is not None:
            existing = _unique_option_labels(await associated.all_inner_texts())
            if existing:
                return existing
        await control.evaluate(
            """element => {
              element.focus({preventScroll: true});
              element.click();
            }"""
        )
        # Some portals fetch choices after the control opens. A fixed short delay can
        # let a late popup from the previous field appear during the next probe and shift
        # every option list onto the wrong question. Wait for a list that is explicitly
        # owned by, or structurally isolated with, this control instead.
        for _ in range(25):
            options = await associated_option_locator(page, control)
            if options is not None:
                values = _unique_option_labels(await options.all_inner_texts())
                if values:
                    return values
            await page.wait_for_timeout(100)
        return []
    except Exception:
        return []
    finally:
        try:
            await control.press("Escape", timeout=1_000)
            await control.evaluate("element => element.blur()")
            await page.locator("[data-jaa-associated-listbox]").evaluate_all(
                "elements => elements.forEach(element => element.removeAttribute('data-jaa-associated-listbox'))"
            )
        except Exception:
            pass


async def _controlled_option_locator(page: Page, control: Locator) -> Locator | None:
    controlled_ids: list[str] = []
    for attribute in ("aria-controls", "aria-owns"):
        for controlled_id in ((await control.get_attribute(attribute)) or "").split():
            if controlled_id not in controlled_ids:
                controlled_ids.append(controlled_id)
    for controlled_id in controlled_ids:
        controlled = page.locator(f"[id={json.dumps(controlled_id)}]")
        if await controlled.count() == 1:
            return controlled.locator("[role='option']")
    return None


async def associated_option_locator(page: Page, control: Locator) -> Locator | None:
    controlled = await _controlled_option_locator(page, control)
    if controlled is not None:
        return controlled

    await page.locator("[data-jaa-associated-listbox]").evaluate_all(
        "elements => elements.forEach(element => element.removeAttribute('data-jaa-associated-listbox'))"
    )
    associated = await control.evaluate(
        """element => {
          let ancestor = element.parentElement;
          while (ancestor && ancestor !== document.documentElement) {
            if (ancestor.matches('form, [data-jaa-application-form]')) return false;
            const controls = Array.from(ancestor.querySelectorAll('[role="combobox"]'));
            const allLists = Array.from(ancestor.querySelectorAll('[role="listbox"]'))
              .filter(list => list.querySelector('[role="option"]'));
            if (controls.length === 1 && controls[0] === element && allLists.length === 1) {
              allLists[0].setAttribute('data-jaa-associated-listbox', 'active');
              return true;
            }
            if (controls.length > 1) return false;
            ancestor = ancestor.parentElement;
          }
          return false;
        }"""
    )
    if not associated:
        return None
    return page.locator("[data-jaa-associated-listbox='active'] [role='option']")


def _unique_option_labels(raw_options: list[str]) -> list[str]:
    values: list[str] = []
    for raw in raw_options:
        value = " ".join(raw.split())
        if value and value not in values:
            values.append(value)
        if len(values) >= 500:
            break
    return values


async def form_fingerprint(page: Page, form: Locator, fields: list[DetectedField]) -> str:
    metadata = await form.evaluate(
        """element => ({
          id: element.id || null,
          name: element.getAttribute('name'),
          action: element.getAttribute('action'),
          method: (element.getAttribute('method') || 'get').toLowerCase()
        })"""
    )
    payload = {
        "url": urldefrag(page.url)[0],
        "form": metadata,
        "fields": [
            {
                "selector": field.selector,
                "question": field.question,
                "type": field.field_type,
                "required": field.required,
                "limit": field.character_limit,
                "options": [] if field.field_type == "combobox" else field.options,
            }
            for field in fields
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()
