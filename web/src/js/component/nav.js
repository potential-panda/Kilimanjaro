function createData(hash) {
  return {
    navLinks: [
      { name: 'Jobs', url: '#jobs', active: hash === 'jobs' },
      { name: 'Results', url: '#results', active: hash === 'results' },
      { name: 'New', url: '#new', active: hash === 'new' }
    ],
    currentPage: hash,
  };
}

W.component('nav', {
  init: function() {
    const hash = window.location.hash.substring(1) || 'jobs';
    this.data = createData(hash);
    this.app.nav.on(() => {
      const hash = window.location.hash.substring(1) || 'jobs';
      this.data = createData(hash);
      this.load();
    });
  },
  getData: function(cb) {
    cb(this.data);
  },
});
