'use strict';
(function(root) {
  function folderTree(feeds) {
    const tree={children:new Map(),feeds:[],path:null};
    for (const feed of feeds) {
      const parts=feed.folder ? feed.folder.split('/').filter(Boolean) : [''];
      let node=tree, path='';
      for(const part of parts) {
        path=path ? path+'/'+part : part;
        if(!node.children.has(part)) node.children.set(part,{name:part || '未分组',path,children:new Map(),feeds:[]});
        node=node.children.get(part);
      }
      node.feeds.push(feed);
    }
    function count(node) {
      node.count=node.feeds.length;node.unread=node.feeds.reduce((n,f)=>n+f.unread,0);node.errors=node.feeds.filter(f=>f.error).length;
      for(const child of node.children.values()) {count(child);node.count+=child.count;node.unread+=child.unread;node.errors+=child.errors;}
      return node;
    }
    return count(tree);
  }
  function paths(tree) { const list=[]; for(const node of tree.children.values()) {list.push(node.path);list.push(...paths(node));}return list; }
  function renamePath(path,oldName,newName) {return path===oldName ? newName : oldName && path.startsWith(oldName+'/') ? newName+path.slice(oldName.length) : path;}
  const api={folderTree,paths,renamePath};root.HarborFolders=api;
  if(typeof module!=='undefined') module.exports=api;
})(globalThis);
