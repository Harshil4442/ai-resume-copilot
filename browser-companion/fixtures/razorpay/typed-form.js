// Locally fulfilled synthetic ReactSelect shape. Every supported ID, option and
// value is supplied by the owned SQL application's real field mapping.
const escape = (value) => String(value).replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

export function typedFixtureHtml(mapping, targetUrl, { unknownOption = false, conditional = false } = {}) {
  const fields = mapping.map((field) => field.type !== "combobox"
    ? `<label for="${field.id}">${escape(field.label)}*</label><input id="${field.id}" type="${field.type}" maxlength="255" aria-required="true">`
    : `<div class="select"><div class="select__container"><label id="${field.id}-label" for="${field.id}">${escape(field.label)}*</label>
      <div class="select-shell"><span id="react-select-${field.id}-live-region"></span><span aria-live="polite" role="log"></span>
      <div class="select__control remix-css-13cymwt-control"><div class="select__value-container"><div class="select__placeholder" id="react-select-${field.id}-placeholder">Select...</div>
      <div class="select__input-container" data-value=""><input id="${field.id}" type="text" class="select__input" role="combobox" aria-expanded="false" aria-labelledby="${field.id}-label" aria-required="true" aria-describedby="react-select-${field.id}-placeholder ${field.id}-error" value=""></div></div>
      <button type="button" aria-label="Toggle flyout" tabindex="-1">Open</button></div><input required tabindex="-1" aria-hidden="true" value=""></div></div></div><span id="${field.id}-error"></span>`).join("");
  return `<!doctype html><html><head><title>Owned typed assistance fixture</title></head><body><form action="${escape(targetUrl)}" method="get">${fields}
    <label for="country">Country*</label><input id="country" role="combobox" aria-required="true">
    <label for="company-name-0">Company name*</label><input id="company-name-0" aria-required="true">
    <label for="resume">Resume/CV*</label><input id="resume" type="file"><button type="submit">Submit manually</button></form>
    <script>
    window.events={input:[],change:[],blur:[],submit:0,choice:[],opens:[]};
    const form=document.querySelector('form');
    form.addEventListener('input',e=>events.input.push(e.target.id));form.addEventListener('change',e=>events.change.push(e.target.id));
    document.querySelector('#email').addEventListener('blur',()=>events.blur.push('email'));
    form.addEventListener('submit',e=>{e.preventDefault();events.submit++});
    const mapping=${JSON.stringify(mapping)};
    for(const field of mapping.filter(field=>field.type==='combobox')){
      const input=document.getElementById(field.id),widget=input.closest('div.select');
      input.addEventListener('click',()=>{
        events.opens.push(field.id);widget.querySelector('.select__control').className='select__control select__control--is-focused select__control--menu-is-open remix-css-yt0d77-control';input.setAttribute('aria-expanded','true');input.setAttribute('aria-controls','react-select-'+input.id+'-listbox');
        const list=document.createElement('div');list.className='select__menu';list.id='react-select-'+input.id+'-listbox';list.setAttribute('role','listbox');
        for(const label of [...field.options.map(option=>option.label),...(${JSON.stringify(unknownOption)}?['Unexpected employer option']:[])]){
          const option=document.createElement('div');option.className='select__option';option.setAttribute('role','option');option.setAttribute('aria-disabled','false');option.textContent=label;
          option.addEventListener('click',()=>{events.choice.push({id:field.id,label});widget.querySelector('.select__placeholder')?.remove();
            const selected=document.createElement('div');selected.className='select__single-value';selected.textContent=label;widget.querySelector('.select__value-container').prepend(selected);
            input.setAttribute('aria-describedby',input.id+'-error');input.setAttribute('aria-expanded','false');input.removeAttribute('aria-controls');list.remove();
            if(${JSON.stringify(conditional)}){const extra=document.createElement('input');extra.id='new-conditional-required';extra.required=true;form.append(extra);}});
          list.append(option);
        }widget.querySelector('.select-shell').append(list);
      });
    }</script></body></html>`;
}
