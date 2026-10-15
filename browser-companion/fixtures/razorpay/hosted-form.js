// Hand-authored controls from the 2026-10-15 blank hosted form observation.
// Post-click row creation/current-role conditional behavior is modeled locally;
// compatibility with those live callbacks has not been asserted.
const escape = (value) => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
export function hostedFixtureHtml(mapping, targetUrl, { extraConditional = false, wrongCountryFlag = false, currentRoleConditional = true } = {}) {
  const render = (field) => {
    if (field.type === 'button') return '<button class="add-another-button" type="button">Add another</button>';
    if (field.type === 'checkbox') return `<label for="${field.id}">Current role</label><input id="${field.id}" name="${field.id.slice(0,-2)}" type="checkbox" value="1" aria-required="false">`;
    if (field.type !== 'combobox') return `<div class="text-input-wrapper"><label for="${field.id}">${escape(field.label)}*</label><input id="${field.id}" type="${field.type}" maxlength="${field.id.includes('-year-') ? 4 : 255}" aria-required="true"></div>`;
    return `<div class="select"><div class="select__container"><label id="${field.id}-label" for="${field.id}">${escape(field.label)}*</label>
    <div class="select-shell"><span id="react-select-${field.id}-live-region"></span><span aria-live="polite" role="log"></span>
    <div class="select__control remix-css-13cymwt-control"><div class="select__value-container"><div class="select__placeholder" id="react-select-${field.id}-placeholder">Select...</div>
    <div class="select__input-container" data-value=""><input id="${field.id}" type="text" class="select__input" role="combobox" aria-expanded="false" aria-labelledby="${field.id}-label" aria-required="true" aria-describedby="react-select-${field.id}-placeholder ${field.id}-error" value=""></div></div><button type="button" aria-label="Toggle flyout" tabindex="-1">Open</button></div><input required tabindex="-1" aria-hidden="true" value=""></div></div></div><span id="${field.id}-error"></span>`;
  };
  const rowField = (field) => /^(company-name|title|start-date-|end-date-|current-role-)/.test(field.id);
  const rows = [...new Set(mapping.filter(rowField).map(field => Number(field.id.match(/-([0-4])(?:_1)?$/)[1])))];
  const body = mapping.filter(field => !rowField(field) && field.type !== 'button').map(render).join('');
  const employment = rows.map(index => `<div class="employment-form" data-row="${index}">${mapping.filter(field => rowField(field) && new RegExp('-'+index+'(?:_1)?$').test(field.id)).map(render).join('')}</div>`).join('');
  return `<!doctype html><html><head><meta charset="utf-8"><title>Owned hosted controls fixture</title></head><body><form action="${escape(targetUrl)}" method="get">${body}<div class="employment--container">${employment}${mapping.filter(field=>field.type==='button').map(render).join('')}</div><label for="resume">Resume/CV*</label><input id="resume" type="file"><button type="submit">Submit manually</button></form>
  <script>
  window.events={input:[],change:[],blur:[],submit:0,choice:[],opens:[],add:0,current:[]};
  const form=document.querySelector('form'); form.addEventListener('input',e=>events.input.push(e.target.id));form.addEventListener('change',e=>events.change.push(e.target.id));
  document.getElementById('email').addEventListener('blur',()=>events.blur.push('email'));form.addEventListener('submit',e=>{e.preventDefault();events.submit++});
  const mapping=${JSON.stringify(mapping)};
  function bind(root){
    for(const input of root.querySelectorAll('input[role=combobox]')){
      const field=mapping.find(field=>field.id===input.id)||mapping.find(field=>field.id===input.id.replace(/-[0-4]$/,'-0'));
      const widget=input.closest('div.select');input.addEventListener('click',()=>{
        events.opens.push(input.id);widget.querySelector('.select__control').className='select__control select__control--is-focused select__control--menu-is-open remix-css-yt0d77-control';input.setAttribute('aria-expanded','true');input.setAttribute('aria-controls','react-select-'+input.id+'-listbox');
        const list=document.createElement('div');list.className='select__menu';list.id='react-select-'+input.id+'-listbox';list.setAttribute('role','listbox');
        for(const value of field.options){const option=document.createElement('div');option.className='select__option';option.setAttribute('role','option');option.setAttribute('aria-disabled','false');option.textContent=value.label;
          if(input.id==='country'){const flag=document.createElement('div');flag.className='iti__flag iti__'+(${JSON.stringify(wrongCountryFlag)}&&value.value==='in'?'us':value.value);option.prepend(flag);}
          option.addEventListener('click',()=>{events.choice.push({id:input.id,value:value.value,label:value.label});widget.querySelector('.select__placeholder')?.remove();const selected=document.createElement('div');selected.className='select__single-value';selected.textContent=value.label;widget.querySelector('.select__value-container').prepend(selected);input.setAttribute('aria-describedby',input.id+'-error');input.setAttribute('aria-expanded','false');input.removeAttribute('aria-controls');list.remove();
          if(${JSON.stringify(extraConditional)}){const extra=document.createElement('input');extra.id='unreviewed-conditional';extra.required=true;form.append(extra);}});list.append(option);
        }widget.querySelector('.select-shell').append(list);
      });
    }
    for(const input of root.querySelectorAll('input[type=checkbox]'))input.addEventListener('change',()=>{events.current.push({id:input.id,checked:input.checked});if(input.checked&&${JSON.stringify(currentRoleConditional)}){const i=input.id.match(/-([0-4])_/)[1];document.getElementById('end-date-month-'+i)?.closest('div.select').remove();document.getElementById('end-date-year-'+i)?.closest('.text-input-wrapper').remove();}});
  }
  const rowTemplate=document.querySelector('.employment-form').outerHTML;bind(form);
  document.querySelector('.add-another-button')?.addEventListener('click',()=>{events.add++;const container=document.querySelector('.employment--container'),index=container.querySelectorAll('.employment-form').length;const template=document.createElement('template');template.innerHTML=rowTemplate.replaceAll('-0','-'+index).replace('data-row="0"','data-row="'+index+'"');const row=template.content.firstElementChild;container.insertBefore(row,container.querySelector('.add-another-button'));bind(row);if(${JSON.stringify(extraConditional)}){const extra=document.createElement('input');extra.id='unreviewed-add-conditional';form.append(extra);}});
  </script></body></html>`;
}
