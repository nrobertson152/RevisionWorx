const txt = (p, s) => p.locator(s).first().innerText();
const assert = (c, m) => { if (!c) throw new Error('ASSERT: ' + m); };
const statusOf = p => p.locator('[role=status]').innerText();
module.exports = [
  { name: 'hover', shot: false, run: async (p) => {
      const row = p.getByRole('button', { name: /Port Plate/ });
      const look = (l) => l.evaluate(e => getComputedStyle(e).boxShadow);
      const before = await look(row); await row.hover(); await p.waitForTimeout(150); const after = await look(row);
      assert(before !== after, 'row hover changes: ' + before + ' -> ' + after);
      console.log('hover ok:', after);
  } },
  { name: 'hover_disabled', file: 'ReadOnly.dc.html', shot: false, run: async (p) => {
      const b = p.getByRole('button', { name: 'Add files' });
      await b.hover({ force: true }); await p.waitForTimeout(150);
      const v = await b.evaluate(e => getComputedStyle(e).boxShadow);
      assert(v === 'none', 'disabled control must not react to hover: ' + v);
  } },
  { name: 'flow_checkin', run: async (p, shot) => {
      await p.getByRole('button', { name: /Manifold Assembly/ }).click(); await p.waitForTimeout(100);
      await shot('flow_1_edited');
      assert((await statusOf(p)).includes('You changed this file'), 'edited status');
      await p.getByRole('button', { name: 'Check in', exact: true }).click(); await p.waitForTimeout(100);
      const submit = p.locator('form[aria-label="Check in"] button[type=submit]');
      assert(await submit.isDisabled(), 'submit disabled while empty');
      await p.locator('textarea').fill('Added a test port on the pressure line');
      assert(!(await submit.isDisabled()), 'submit enabled once described');
      await shot('flow_2_dialog');
      await submit.click(); await p.waitForTimeout(150);
      assert((await statusOf(p)).includes('Saved as revision E'), 'saved flash: ' + await statusOf(p));
      assert((await p.locator('section[aria-label=History]').innerText()).includes('Added a test port'), 'history has note');
      const rowText = await p.getByRole('button', { name: /Manifold Assembly/ }).innerText();
      assert(!rowText.includes('Edited') && /\bE\b/.test(rowText), 'row updated: ' + rowText);
      await shot('flow_3_saved');
      // check out again, edit, cancel with discard
      await p.getByRole('button', { name: 'Check out', exact: true }).click(); await p.waitForTimeout(100);
      assert((await statusOf(p)).includes('Checked out by you'), 'checked out');
      await p.getByRole('button', { name: 'Open file' }).click(); await p.waitForTimeout(100);
      assert((await statusOf(p)).includes('You changed this file'), 'open edits');
      await p.getByRole('button', { name: 'More actions' }).click(); await p.waitForTimeout(100);
      await shot('flow_4_more');
      await p.getByRole('button', { name: 'Cancel checkout' }).click(); await p.waitForTimeout(100);
      await shot('flow_5_cancel');
      await p.getByRole('button', { name: 'Discard my changes' }).click(); await p.waitForTimeout(100);
      assert((await statusOf(p)).includes('Checkout cancelled'), 'cancelled');
      // history: expand, compare, restore hint then restore while checked out
      await p.getByRole('button', { name: /^Revision C:/ }).click(); await p.waitForTimeout(100);
      assert((await p.locator('section[aria-label=History]').innerText()).includes('Check the document out to restore'), 'restore hint');
      await p.getByRole('button', { name: /Compare with B/ }).click(); await p.waitForTimeout(100);
      await shot('flow_6_rev_open');
      await p.getByRole('button', { name: 'Check out', exact: true }).click(); await p.waitForTimeout(100);
      await p.getByRole('button', { name: /^Revision B:/ }).click(); await p.waitForTimeout(100);
      await p.getByRole('button', { name: 'Restore', exact: true }).click(); await p.waitForTimeout(100);
      assert((await statusOf(p)).includes('File replaced with revision B'), 'restored');
      // lifecycle menu with outside click closing
      await p.getByRole('button', { name: /Seal Kit/ }).click(); await p.waitForTimeout(100);
      await p.getByRole('button', { name: /^Approved/ }).click(); await p.waitForTimeout(100);
      await shot('flow_7_state_menu');
      await p.getByRole('button', { name: 'Close menu' }).click({ position: { x: 5, y: 5 } }); await p.waitForTimeout(100);
      assert(await p.getByRole('button', { name: 'Release', exact: true }).count() === 0, 'menu closes on outside click');
      await p.getByRole('button', { name: /^Approved/ }).click(); await p.waitForTimeout(100);
      await p.getByRole('button', { name: 'Release', exact: true }).click(); await p.waitForTimeout(100);
      assert((await statusOf(p)).includes('is now Released'), 'released');
      // filters and search
      await p.getByRole('button', { name: /Needs attention/ }).click(); await p.waitForTimeout(100);
      assert((await p.locator('section[aria-label=Documents]').innerText()).includes('Mounting Bracket'), 'attention filter');
      await p.getByRole('button', { name: /^All/ }).click(); await p.waitForTimeout(100);
      await p.getByLabel('Filter this project').fill('zzz'); await p.waitForTimeout(100);
      assert((await p.locator('section[aria-label=Documents]').innerText()).includes('No documents match'), 'no matches');
      await shot('flow_8_no_match');
      await p.getByRole('button', { name: 'Show all documents' }).click(); await p.waitForTimeout(100);
      // new project then add a file
      await p.getByRole('button', { name: 'New project' }).click(); await p.waitForTimeout(100);
      await p.locator('form[aria-label="New project"] input').first().fill('Accumulator Rig');
      await p.locator('form[aria-label="New project"] button[type=submit]').click(); await p.waitForTimeout(100);
      assert((await p.locator('main').innerText()).includes('Add your first file'), 'new project empty');
      await p.getByRole('button', { name: 'Choose files' }).click(); await p.waitForTimeout(100);
      assert((await statusOf(p)).includes('Added as revision A'), 'file added');
  } },
  { name: 'dark', file: 'Dark.dc.html' },
  { name: 'checkin', file: 'CheckIn.dc.html' },
  { name: 'attention', file: 'Attention.dc.html' },
  { name: 'readonly', file: 'ReadOnly.dc.html', run: async (p) => {
      assert(await p.getByRole('button', { name: 'Check out', exact: true }).isDisabled(), 'read-only disables primary');
      assert(await p.getByRole('button', { name: 'Add files' }).isDisabled(), 'read-only disables add');
      await p.getByRole('button', { name: 'Show details' }).click(); await p.waitForTimeout(100);
  } },
  { name: 'empty', file: 'EmptyProject.dc.html', check: () => document.querySelector('nav').innerText.includes('1') },
  { name: 'welcome', file: 'Welcome.dc.html', run: async (p, shot) => {
      await shot('welcome_blank');
      assert(await p.getByRole('button', { name: 'Create project' }).isDisabled(), 'needs a name');
      await p.getByLabel('Project name').fill('Hydraulic Manifold');
      await p.getByRole('button', { name: 'Create project' }).click(); await p.waitForTimeout(150);
      assert((await p.locator('main').innerText()).includes('Add your first file'), 'welcome -> empty project');
  } },
  { name: 'focus_list', file: 'OneAtATime.dc.html', run: async (p, shot) => {
      await shot('focus_list');
      await p.getByRole('button', { name: /Manifold Assembly/ }).click(); await p.waitForTimeout(100);
      assert(await p.getByRole('button', { name: 'All documents' }).count() === 1, 'back button');
      await shot('focus_doc');
      await p.getByRole('button', { name: 'All documents' }).click(); await p.waitForTimeout(100);
      assert((await p.locator('section[aria-label=Documents]').count()) === 1, 'back to list');
  } },
  { name: 'phone', w: 390, h: 844, full: true, check: () => ({ scrollW: document.documentElement.scrollWidth, clientW: document.documentElement.clientWidth }) },
  { name: 'tablet', w: 900, h: 800, check: () => ({ scrollW: document.documentElement.scrollWidth, clientW: document.documentElement.clientWidth }) },
];
