// Import Kilimanjaro components
import './component/new.js';
import './component/jobs.js';
import './component/results.js';
import './component/nav.js';
import './component/header.js';
import './component/delete_button.js';
import './component/hidden_columns.js';
import { client, hiddenColumns } from './data.js';

function getTemplate(name, cb) {
  import('./template/' + name + '.html').then((tmpl) => {
    cb(tmpl.default);
  });
}

const app = W.app('kilimanjaro', getTemplate);

W.switch('pages', {
  knownComponents: {
    'new': 'new',
    'jobs': 'jobs',
    'results': 'results',
    }
  }
);

// Attach client to app.data for direct access
app.data.client = client;

app.data.register('jobs', client.fetchJobs);
app.data.register('results', client.fetchResults);
app.data.register('hiddenColumns', hiddenColumns.fetch);
app.start(document.body);
