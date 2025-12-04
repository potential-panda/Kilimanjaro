export function getUrlParam(name) {
  const hash = window.location.hash;
  const params = new URLSearchParams(hash.split('?')[1] || '');
  return params.get(name);
}

const _RE = /^#([^\/\?]+)/;

export function getPage() {
  const match = _RE.exec(window.location.hash);
  return (match && match[1]) || '';
}
