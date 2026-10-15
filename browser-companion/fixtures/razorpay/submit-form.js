// Hand-authored public uploader shape; every request is fulfilled locally.
// This fixture is not employer response authenticity or live upload acceptance.
export function submitFixtureHtml(mapping, values, target) {
  const escape=(value)=>String(value).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;');
  return `<!doctype html><html><head><title>Owned final-submit fixture</title><style>.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}</style></head><body>
  <form id="application-form" action="${target}" method="get">${mapping.map(field=>`<label for="${field.id}">${field.label}*</label><input id="${field.id}" type="${field.type}" maxlength="255" aria-required="true" value="${escape(values[field.id])}">`).join('')}
  <div id="upload-label-resume">Resume/CV*</div><div class="file-upload" aria-labelledby="upload-label-resume"><label for="resume">Attach</label><input class="visually-hidden" id="resume" type="file"></div><button type="submit">Submit application</button></form>
  <script>window.events={input:[],change:[],blur:[],submit:0,file:0};const form=document.querySelector('form');
  form.addEventListener('input',event=>events.input.push(event.target.id));
  form.addEventListener('change',async event=>{events.change.push(event.target.id);if(event.target.id!=='resume')return;events.file++;const file=event.target.files[0],group=event.target.closest('.file-upload');group.innerHTML='<progress role="progressbar"></progress>';const response=await fetch('${target}/owned-synthetic-upload',{method:'POST',body:file});if(response.ok){group.innerHTML='<div class="file-upload__filename"><p class="body__secondary"></p><button type="button" aria-label="Remove file">Remove file</button></div>';group.querySelector('p').textContent=file.name;}});
  form.addEventListener('submit',async event=>{event.preventDefault();events.submit++;const response=await fetch('${target}/owned-synthetic-submit',{method:'POST',body:new FormData(form)});if(response.ok)location.href='${target}/confirmation';});</script></body></html>`;
}
