extern int bias(void);
static volatile int value = 30;
int answer(void) { return value + bias(); }
