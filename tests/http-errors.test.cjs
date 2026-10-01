const assert=require('node:assert/strict');
const {test}=require('node:test');
const {message}=require('../static/http-errors.js');

test('Cloudflare HTML becomes a readable gateway error',()=>{
  const html='<!DOCTYPE html><html><head><title>502: Bad gateway</title></head><body>Cloudflare</body></html>';
  assert.match(message(502,html),/^Gateway error:/);
  assert.ok(!message(502,html).includes('<html>'));
});
test('SSH error details remain available',()=>{
  assert.equal(message(502,JSON.stringify({detail:'SSH host fingerprint does not match.'})),'SSH host fingerprint does not match.');
});
test('validation errors keep field names',()=>{
  assert.equal(message(422,JSON.stringify({detail:[{loc:['body','port'],msg:'Input should be greater than or equal to 1'}]})),'port: Input should be greater than or equal to 1');
});
test('empty responses and access pages have useful fallbacks',()=>{
  assert.match(message(504,''),/too long/);
  assert.match(message(403,'<html>Sign in to Access</html>'),/Access was refused/);
  assert.equal(message(418,'<html>unexpected</html>'),'Request failed (HTTP 418).');
});
test('internal error bodies do not expose tracebacks',()=>{
  assert.match(message(500,'Traceback: internal path and data'),/^ServerCP encountered/);
});
