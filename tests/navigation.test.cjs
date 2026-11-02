const {test}=require('node:test');
const assert=require('node:assert/strict');
const {folderTree,paths,renamePath}=require('../app/static/navigation.js');
test('nested folders preserve every feed and aggregate counts',()=>{
  const tree=folderTree([{id:1,folder:'Articles/AI',unread:3,error:''},{id:2,folder:'Articles/Finance',unread:2,error:'403'},{id:3,folder:'Pictures',unread:1,error:''},{id:4,folder:'',unread:4,error:''}]);
  assert.equal(tree.count,4);assert.equal(tree.unread,10);
  const articles=tree.children.get('Articles');assert.equal(articles.count,2);assert.equal(articles.errors,1);
  assert.equal(articles.children.get('AI').feeds[0].id,1);
  assert.equal(tree.children.get('').name,'未分组');
  assert.deepEqual(paths(tree),['Articles','Articles/AI','Articles/Finance','Pictures','']);
});
test('folder keys cannot mutate object prototypes',()=>{
  const tree=folderTree([{id:1,folder:'__proto__/constructor',unread:0,error:''}]);
  assert.equal(tree.children.get('__proto__').children.get('constructor').count,1);
});
test('renaming a subtree leaves similarly named neighbors alone',()=>{
  assert.equal(renamePath('A/B/C','A/B','A/Research'),'A/Research/C');
  assert.equal(renamePath('A/Beta','A/B','A/Research'),'A/Beta');
  assert.equal(renamePath('','', 'Inbox'),'Inbox');
});
