import { getUrlParam } from '../util/url.js';

W.component('tabs', {
  init: function() {
    this.app.data.on("dirs", () => {
      this.load();
    }, this);

    this.app.nav.on(() => {
      this.load();
    }, this);
    // Fetch initial tab data
    this.app.data.fetch("dirs");
  },

  getData: function(cb) {
    const tabsData = this.app.data.get("dirs") || [];
    const currentTab = getUrlParam("dir");

    // Determine active tab
    const activeTab = currentTab || (tabsData.length > 0 ? tabsData[0].name : null);

    // Transform tab data for template
    const tabs = tabsData.map(tab => ({
      name: tab.name,
      isActive: tab.name === activeTab,
      targetHash: `results?dir=${tab.name}`
    }));

    cb({
      tabs: tabs,
      showTabs: tabs.length > 1
    });
  }
});
