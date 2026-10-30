const test=require('node:test');const assert=require('node:assert/strict');
const modes=require('../backend/app/static/replacement-modes.js');
const video={kind:'video',file:'source'},person={kind:'image',file:'character'},location={kind:'image',file:'scene'};
test('optional location selects scene route; person keeps SAM3',()=>{
 assert.equal(modes.mode([video,person]),'person');assert.equal(modes.jobType('person'),'replacement');
 assert.equal(modes.mode([video,person,location]),'all');assert.equal(modes.jobType('all'),'standard');
});
test('only character, optional location and one video accepted',()=>{
 modes.validate('person',[video,person]);modes.validate('all',[video,person,location]);
 for(const refs of [[video],[video,person,location,location],[video,video,person],[video,person,{kind:'audio'}]])assert.throws(()=>modes.validate(modes.mode(refs),refs));
 assert.throws(()=>modes.validate('all',[video,person]));
});
test('slot replacement preserves picture roles and video metadata',()=>{
 const replacement={kind:'image',file:'new-character'},scene={kind:'image',file:'new-scene'};
 assert.deepEqual(modes.replaceSlot([person,location,video],'image',replacement),[replacement,location,video]);
 assert.deepEqual(modes.replaceSlot([person,location,video],'location',scene),[person,scene,video]);
 assert.deepEqual(modes.replaceSlot([person,video],'location',scene),[person,scene,video]);
 assert.throws(()=>modes.replaceSlot([video],'location',scene));
});
test('instructions are deterministic and bind image roles',()=>{
 assert.match(modes.prompts.all,/<Picture 1>/);assert.match(modes.prompts.all,/<Picture 2>/);assert.match(modes.prompts.all,/<Video 1>/);assert.doesNotMatch(modes.prompts.all,/Describe the new/);
});
