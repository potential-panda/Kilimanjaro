W.component('nav', {
  init: function() {
    this.app.nav.on(() => {
      this.load();
    });
  },
  getData: function(cb) {
    const hash = window.location.hash.substring(1) || 'jobs';
    cb({
      navLinks: [
        { name: 'Jobs', url: '#jobs', active: hash === 'jobs' },
        { name: 'Results', url: '#results', active: hash === 'results' },
        { name: 'New', url: '#new', active: hash === 'new' }
      ],
      currentPage: hash,
    });
  },
});
