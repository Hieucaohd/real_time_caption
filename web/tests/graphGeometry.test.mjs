import test from 'node:test';
import assert from 'node:assert/strict';
import {zoomAt,worldPoint,autoLayout,addMissingPositions,visibleNodes,descendants,bounds,fitCamera,edgePath,WIDTH,HEIGHT} from '../src/graphGeometry.js';

const nodes=[{id:'root',parent_id:null},{id:'a',parent_id:'root'},{id:'b',parent_id:'root'},{id:'c',parent_id:'a'}];
test('zoom preserves the world point under the mouse at any zoom and translation',()=>{
  for(const camera of [{x:40,y:50,zoom:1},{x:-900,y:300,zoom:.25},{x:1250,y:-750,zoom:3}]){
    const mouse={x:317,y:201},before=worldPoint(mouse,camera);
    for(const z of [.01,.1,1,4,10]){const after=worldPoint(mouse,zoomAt(camera,mouse,z));assert.ok(Math.abs(before.x-after.x)<1e-8);assert.ok(Math.abs(before.y-after.y)<1e-8);}
  }
});
test('new nodes do not shift manually arranged nodes or collide with existing siblings',()=>{
  const positions={root:{x:-200,y:100},a:{x:110,y:210},b:{x:110,y:320}};
  const next=addMissingPositions(nodes,'root',positions);
  for(const id of Object.keys(positions))assert.deepEqual(next[id],positions[id]);
  assert.ok(next.c.x>=positions.a.x+WIDTH);
});
test('collapsed branches hide descendants without changing logical ancestry',()=>{
  assert.deepEqual(visibleNodes(nodes,new Set(['a'])).map(n=>n.id),['root','a','b']);
  assert.deepEqual([...descendants(nodes,['a','c'])].sort(),['a','c']);
  assert.deepEqual([...descendants(nodes,['root'])].sort(),['a','b','c','root']);
});
test('fit camera contains all nodes even with negative world coordinates',()=>{
  const positions=autoLayout(nodes,'root');positions.a={x:-900,y:-700};positions.b={x:1200,y:1500};
  const box=bounds(nodes,positions),size={width:800,height:600},camera=fitCamera(box,size);
  for(const p of Object.values(positions)){assert.ok(p.x*camera.zoom+camera.x>=0);assert.ok(p.y*camera.zoom+camera.y>=0);assert.ok((p.x+WIDTH)*camera.zoom+camera.x<=size.width);assert.ok((p.y+HEIGHT)*camera.zoom+camera.y<=size.height);}
});
test('connector paths remain valid when a child moves left, above or below its parent',()=>{
  for(const p of [{x:-600,y:10},{x:10,y:-500},{x:10,y:500}]){
    const path=edgePath({x:0,y:0},p);assert.match(path,/^M [-\d.]+ [-\d.]+ C /);assert.ok(!path.includes('NaN'));
  }
});
