const {test}=require('node:test');
const assert=require('node:assert/strict');
const {parseRow,number}=require('../../static/planning/smg_clipboard.js');
test('Russian Excel decimals, spaces and blanks',()=>{
 assert.deepEqual(parseRow('1,25\t2\t\r\n').map(number),['1.25','2','0']);
 for(const value of ['1 234,50','1\u00a0234,50','1\u202f234,50'])assert.equal(number(value),'1234.50');
 for(const value of ['','—','–','-'])assert.equal(number(value),'0');
 assert.equal(number('+2,500000'),'2.500000');
});
test('Quoted fields and whole-row labels are retained for preview',()=>{
 assert.deepEqual(parseRow('"Работа ""А"""\tм\t31\t1\t2\t33\r\n'),['Работа "А"','м','31','1','2','33']);
 assert.deepEqual(parseRow('"Текст\nв две строки"\t1'),['Текст\nв две строки','1']);
});
test('Never concatenate multiline numbers or evaluate formulas',()=>{
 for(const value of ['1\n2','1\t2','-1','NaN','Infinity','1e3','=2+2','1,1234567','1,2,3'])assert.throws(()=>number(value));
});
test('Multiple rows and malformed quotes cannot partly paste',()=>{
 assert.throws(()=>parseRow('1\t2\n3\t4'),/одну строку/);
 assert.throws(()=>parseRow('"1\t2'),/прочитать/);
});
test('Whole Excel row keeps a month of empty days but stays bounded',()=>{
 const row=parseRow('1\t2'+'\t'.repeat(16380)+'\r\n');assert.equal(row.length,33);assert.equal(row[2],'');
 assert.throws(()=>parseRow('1\t'.repeat(502)),/нужный месяц/);
 assert.throws(()=>parseRow('1'.repeat(2*1024*1024+1)),/слишком большой/);
});
