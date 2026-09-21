/* Linker acceptance input: no libc or Linux startup code. */
extern int answer(void);
extern int optional_answer(void) __attribute__((weak));
__attribute__((noreturn)) void _start(void) {
    unsigned long status = answer();
    if (optional_answer) status = 91;
#if defined(__aarch64__)
    register unsigned long x0 __asm__("x0") = status;
    register unsigned long x8 __asm__("x8") = 1;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8) : "memory");
#elif defined(__riscv) && __riscv_xlen == 64
    register unsigned long a0 __asm__("a0") = status;
    register unsigned long a7 __asm__("a7") = 1;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a7) : "memory");
#else
#error unsupported Scarlet architecture
#endif
    for (;;) {}
}
