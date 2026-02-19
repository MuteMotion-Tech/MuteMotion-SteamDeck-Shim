type Listener<T> = (value: T) => void;

class BaseState<T> {
    protected state: T;
    private listeners: Listener<T>[] = [];

    constructor(initialValue: T) {
        this.state = initialValue;
    }

    onStateChanged(callback: Listener<T>) {
        this.listeners.push(callback);
    }

    offStateChanged(callback: Listener<T>) {
        // find this dude and kick him out of the party
        const index = this.listeners.indexOf(callback);
        if (index !== -1) {
            this.listeners.splice(index, 1);
        }
    }

    SetState(value: T) {
        // if nothing happened, im going back to sleep
        if (this.state === value) {
            return;
        }

        this.state = value;
        // yell at everyone that something changed
        this.listeners.forEach(listener => listener(value));
    }

    GetState(): T {
        return this.state;
    }
}

export class StateBoolean extends BaseState<boolean> {
    constructor(initialValue = false) {
        super(initialValue);
    }
}
