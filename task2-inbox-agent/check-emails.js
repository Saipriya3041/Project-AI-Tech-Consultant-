require('ts-node').register({ transpileOnly: true });
const { DEMO_EMAILS } = require('./src/gmail/client');
const { classifyEmail } = require('./src/email/classifier');
console.log('Total DEMO_EMAILS:', DEMO_EMAILS.length);
const cats = {};
DEMO_EMAILS.forEach(e => {
  const cat = classifyEmail(e.subject, e.body);
  cats[cat] = (cats[cat] || 0) + 1;
});
console.log('Categories:', JSON.stringify(cats, null, 2));
