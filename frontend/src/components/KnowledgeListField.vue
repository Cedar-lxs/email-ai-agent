<template>
  <div class="list-field">
    <div class="label"><strong>{{ label }}<em v-if="required"> *</em></strong><el-button text type="primary" :icon="Plus" @click="add">新增</el-button></div>
    <div v-for="(item, index) in modelValue" :key="index" class="row">
      <span>{{ ordered ? `${index + 1}.` : '•' }}</span>
      <el-input :model-value="item" :placeholder="placeholder" @update:model-value="update(index, $event)" />
      <el-button text type="danger" :icon="Delete" :disabled="required && modelValue.length === 1" @click="remove(index)" />
    </div>
    <button v-if="!modelValue.length" type="button" class="empty" @click="add">+ 添加{{ label }}</button>
  </div>
</template>
<script setup>
import { Delete, Plus } from '@element-plus/icons-vue'
const props = defineProps({ modelValue: { type: Array, required: true }, label: String, placeholder: String, ordered: Boolean, required: Boolean })
const emit = defineEmits(['update:modelValue'])
const add = () => emit('update:modelValue', [...props.modelValue, ''])
const remove = index => emit('update:modelValue', props.modelValue.filter((_, i) => i !== index))
const update = (index, value) => { const next = [...props.modelValue]; next[index] = value; emit('update:modelValue', next) }
</script>
<style scoped>
.list-field{margin-bottom:22px}.label{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;color:#344054;font-size:14px}.label em{color:#f56c6c;font-style:normal}.row{display:grid;grid-template-columns:24px 1fr 34px;align-items:center;gap:7px;margin-bottom:8px}.row>span{color:#667085;text-align:right}.empty{width:100%;padding:12px;border:1px dashed #b9c7da;border-radius:8px;background:#fafcff;color:#2563eb;cursor:pointer}
</style>
