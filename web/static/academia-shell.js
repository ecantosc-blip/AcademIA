/* Shared sidebar persistence for both dashboard and study. */
(function(){
 const button=document.getElementById('plegar'), root=document.documentElement;
 if(!button) return;
 function render(){
  const folded=root.getAttribute('data-lateral')==='plegada';
  button.setAttribute('aria-expanded',String(!folded));
  const label=folded?'Mostrar barra lateral':'Ocultar barra lateral';
  button.setAttribute('aria-label',label);button.title=label;
 }
 button.addEventListener('click',function(){
  const folded=root.getAttribute('data-lateral')==='plegada';
  if(folded) root.removeAttribute('data-lateral'); else root.setAttribute('data-lateral','plegada');
  try{localStorage.setItem('lateral',folded?'abierta':'plegada');}catch(e){}
  render();
 });
 render();
})();
